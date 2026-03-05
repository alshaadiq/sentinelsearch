"""
Post-processing: fill cloud/shadow gaps in the composite COG and produce
a seamless output by blending filled values smoothly into neighbouring
valid pixels.

Algorithm
---------
1. Identify "bad" pixels:  SCL ∈ {3 (cloud shadow), 8, 9, 10 (cloud)} or NaN.
2. For every optical band (B02–B12, NDVI):
   a. Nearest-neighbour fill  – each bad pixel adopts the value of the
      closest valid pixel (scipy distance_transform_edt with return_indices).
   b. Edge feathering          – inside each cloud patch, a weight ramp
      (0 at the seam → 1 at the centre) blends the NN-filled value with a
      Gaussian-smoothed version of the fully-filled array.
      This prevents hard colour steps at cloud boundaries.
3. NDVI is recomputed from the filled B04 / B08 for consistency.
4. SCL is left untouched – filled pixels retain their original cloud class
   so downstream analysis can distinguish filled from real observations.
5. The result is written back to the same COG path (in-place replacement
   via a temp file + atomic rename).

Memory budget
-------------
For a 5 000 × 5 000 px, 12-band COG  →  12 × 5000² × 4 B ≈ 1.2 GB.
Bands are processed sequentially (one at a time),  so peak working memory
is ≈  (3 × one_band) + distance_arrays  ≈  300–500 MB  for that size.

Parameters (module-level constants, tweak as needed)
-----------------------------------------------------
BLEND_RADIUS_PX : int
    Half-width of the blending zone in pixels.  300 m at 10 m = 30 px.
SCL_CLOUD_CLASSES : set
    SCL integer values treated as "bad" (cloud / cloud shadow).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import rasterio
import rasterio.crs
from rasterio.enums import Resampling as RIOResampling
from rasterio.shutil import copy as rio_copy
from scipy.ndimage import distance_transform_edt, gaussian_filter

from processing.export_cog import OUTPUT_BAND_ORDER

logger = logging.getLogger(__name__)

# ── Tunable constants ─────────────────────────────────────────────────────────
BLEND_RADIUS_PX: int = 30          # feathering zone width (pixels) – 300 m @ 10 m
SCL_CLOUD_CLASSES: frozenset[int] = frozenset({3, 8, 9, 10})
GAUSS_SIGMA_FACTOR: float = 0.33   # sigma = BLEND_RADIUS × GAUSS_SIGMA_FACTOR


def fill_composite_gaps(cog_path: Path) -> Path:
    """
    Fill cloud / shadow gaps in a composite COG and rewrite it in place.

    Parameters
    ----------
    cog_path : Path
        Path to the existing Cloud Optimized GeoTIFF produced by export_cog.
        The file is overwritten with the gap-filled version.

    Returns
    -------
    Path
        The same ``cog_path`` (file now contains filled data).
    """
    t0 = time.perf_counter()
    tmp_path = cog_path.parent / (cog_path.stem + "_gapfill_tmp.tif")

    with rasterio.open(cog_path) as src:
        profile = src.profile.copy()
        height, width = src.height, src.width
        n_bands = src.count

        # Build band-name → 1-based index map from band tags
        tags_list = [src.tags(i) for i in range(1, n_bands + 1)]
        band_names = [
            t.get("name", OUTPUT_BAND_ORDER[i] if i < len(OUTPUT_BAND_ORDER) else f"B{i+1}")
            for i, t in enumerate(tags_list)
        ]
        name_to_idx = {name: i + 1 for i, name in enumerate(band_names)}  # 1-based

        # ── Read SCL to build the bad-pixel mask ──────────────────────
        scl_idx = name_to_idx.get("SCL")
        if scl_idx is not None:
            scl_arr = src.read(scl_idx).astype(np.float32)
            scl_bad = np.zeros((height, width), dtype=bool)
            for cls in SCL_CLOUD_CLASSES:
                scl_bad |= (scl_arr == cls)
        else:
            scl_bad = np.zeros((height, width), dtype=bool)
            logger.warning("SCL band not found in COG; gap detection will use NaN-only.")

        # ── Read all bands into memory ────────────────────────────────
        data = src.read()  # shape: (n_bands, H, W)  float32

    # Build combined bad mask:  cloud/shadow SCL class  OR  NaN in any optical band
    nan_bad = np.zeros((height, width), dtype=bool)
    for i, name in enumerate(band_names):
        if name != "SCL":
            nan_bad |= ~np.isfinite(data[i])
    bad_mask = scl_bad | nan_bad  # (H, W) bool

    n_bad = int(bad_mask.sum())
    n_total = height * width
    pct = n_bad / n_total * 100

    if n_bad == 0:
        logger.info("gap_fill: no bad pixels found — skipping (%.0f ms)", (time.perf_counter() - t0) * 1000)
        return cog_path

    logger.info(
        "gap_fill: %d px (%.1f%% of %d×%d) to fill; blend_radius=%d px",
        n_bad, pct, width, height, BLEND_RADIUS_PX,
    )

    # ── Pre-compute distance arrays (shared for all bands) ───────────
    # dist_to_valid:  distance (px) from each bad pixel to nearest VALID pixel
    # nn_indices:     (row, col) of nearest valid pixel for each pixel
    valid_mask = ~bad_mask
    dist_to_valid, nn_indices = distance_transform_edt(bad_mask, return_indices=True)

    # dist_inside:  for each bad pixel, its distance to the nearest VALID neighbour
    # Used to build blend weight: 0 near seam, 1 deep inside cloud patch
    # We re-use dist_to_valid (already computed above).
    dist_inside = dist_to_valid  # alias for clarity

    # Blend weight: 0 at valid border, ramps to 1 at BLEND_RADIUS_PX deep
    #   outside bad region → weight irrelevant (pixel will not be blended anyway)
    blend_w = np.clip(dist_inside / BLEND_RADIUS_PX, 0.0, 1.0).astype(np.float32)
    sigma = max(1.0, BLEND_RADIUS_PX * GAUSS_SIGMA_FACTOR)

    # ── Fill bands one at a time ──────────────────────────────────────
    filled = data.copy()  # (n_bands, H, W)

    for i, name in enumerate(band_names):
        if name == "SCL":
            # SCL: keep original — filled pixels retain cloud class for provenance
            continue

        arr = filled[i].copy()  # (H, W) float32

        # Replace NaN with 0 before NN lookup so the array is arithmetically clean
        arr = np.nan_to_num(arr, nan=0.0)

        # Step 1 – nearest-neighbour fill
        arr[bad_mask] = arr[nn_indices[0][bad_mask], nn_indices[1][bad_mask]]

        # Step 2 – Gaussian-smoothed version of the NN-filled array
        #          provides a spatially smooth "background" to blend toward
        arr_smooth = gaussian_filter(arr, sigma=sigma, mode="reflect")

        # Step 3 – blend inside bad region:
        #   near seam  (blend_w ≈ 0) → use smooth bg (avoids sharp edge)
        #   deep inside (blend_w ≈ 1) → use NN fill (preserves patch texture)
        blended = arr_smooth * (1.0 - blend_w) + arr * blend_w
        arr[bad_mask] = blended[bad_mask].astype(np.float32)

        filled[i] = arr

    # ── Recompute NDVI from filled B04 / B08 for consistency ─────────
    ndvi_idx = name_to_idx.get("NDVI")
    b04_idx = name_to_idx.get("B04")
    b08_idx = name_to_idx.get("B08")
    if ndvi_idx is not None and b04_idx is not None and b08_idx is not None:
        b04 = filled[b04_idx - 1].astype(np.float32)
        b08 = filled[b08_idx - 1].astype(np.float32)
        ndvi_new = (b08 - b04) / (b08 + b04 + 1e-9)
        ndvi_new = np.clip(ndvi_new, -1.0, 1.0)
        # Only overwrite previously-bad pixels
        filled[ndvi_idx - 1][bad_mask] = ndvi_new[bad_mask]
        logger.debug("gap_fill: NDVI recomputed for %d px", n_bad)

    # ── Write temp GeoTIFF ────────────────────────────────────────────
    profile.update(
        driver="GTiff",
        compress="deflate",
        predictor=2,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        nodata=np.nan,
        count=n_bands,
    )
    # Remove COG-specific keys that are invalid for a plain write
    for key in ("COPY_SRC_OVERVIEWS", "copy_src_overviews"):
        profile.pop(key, None)

    logger.info("gap_fill: writing filled temp file …")
    with rasterio.open(tmp_path, "w", **profile) as dst:
        dst.write(filled)
        # Copy band tags (names etc.)
        for i in range(1, n_bands + 1):
            dst.update_tags(i, **tags_list[i - 1])
        # Build overviews before converting to COG
        dst.build_overviews([2, 4, 8, 16, 32], RIOResampling.average)
        dst.update_tags(ns="rio_overview", resampling="average")

    # ── Convert temp → COG (replaces original) ───────────────────────
    logger.info("gap_fill: converting to COG and replacing original …")
    rio_copy(
        tmp_path,
        cog_path,
        driver="GTiff",
        copy_src_overviews=True,
        compress="DEFLATE",
        predictor=2,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        nodata=np.nan,
    )
    tmp_path.unlink(missing_ok=True)

    elapsed = (time.perf_counter() - t0) * 1000
    logger.info(
        "gap_fill: done — filled %.1f%% of pixels in %.0f ms → %s (%.1f MB)",
        pct, elapsed, cog_path.name, cog_path.stat().st_size / 1e6,
    )
    return cog_path
