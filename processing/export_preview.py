"""
Generate a small RGB PNG quicklook from the composite COG.

- Reads B04 (Red), B03 (Green), B02 (Blue) from the COG
- Applies 2–98 percentile stretch per channel
- Downsamples to max 768 px on the longer axis
- Saves as PNG with JPEG-like quality (lossless PNG)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
import rasterio.enums
from PIL import Image

from backend.config import settings
from processing.export_cog import OUTPUT_BAND_ORDER

logger = logging.getLogger(__name__)

PREVIEW_MAX_DIM = 768  # max pixels on the longer side


def export_preview(cog_path: Path, job_id: str) -> Path:
    """
    Create a PNG quicklook from an existing COG.

    Parameters
    ----------
    cog_path : Path
        Path to the Cloud Optimized GeoTIFF produced by :func:`export_cog`.
    job_id : str
        Used to name the output preview file.

    Returns
    -------
    Path
        Absolute path to the PNG preview.
    """
    preview_path = settings.previews_dir / f"{job_id}.png"

    with rasterio.open(cog_path) as src:
        # Determine preview output size (respecting aspect ratio)
        native_h, native_w = src.height, src.width
        scale = min(PREVIEW_MAX_DIM / native_h, PREVIEW_MAX_DIM / native_w, 1.0)
        out_h = max(1, int(native_h * scale))
        out_w = max(1, int(native_w * scale))

        # Identify band indices for B04, B03, B02 in the COG
        tags_list = [src.tags(i) for i in range(1, src.count + 1)]
        band_names_in_cog = [t.get("name", OUTPUT_BAND_ORDER[i]) for i, t in enumerate(tags_list)]

        def _band_index(name: str) -> int:
            """1-based index of a named band in the COG."""
            try:
                return band_names_in_cog.index(name) + 1
            except ValueError:
                return None

        rgb_indices = [_band_index("B04"), _band_index("B03"), _band_index("B02")]

        # Fall back to first three bands if RGB not found
        rgb_indices = [i if i is not None else (j + 1) for j, i in enumerate(rgb_indices)]

        channels = []
        nodata_val = src.nodata
        for band_idx in rgb_indices:
            raw = src.read(
                band_idx,
                out_shape=(out_h, out_w),
                resampling=rasterio.enums.Resampling.average,
            ).astype(np.float32)
            # Mask explicit nodata value
            if nodata_val is not None and not np.isnan(nodata_val):
                raw[raw == nodata_val] = np.nan
            # Sentinel-2 L2A reflectance: 0–10000 DN.  Anything outside is nodata/garbage.
            raw[(raw < 0) | (raw > 10000)] = np.nan
            channels.append(raw)

    # ── Linked stretch across all three channels ──────────────────────
    # KEY: use a SINGLE shared lo/hi derived from all three channels pooled.
    # Independent per-channel stretch breaks R:G:B ratios:
    #   water (B04≈100, B03≈200, B02≈500) → independent stretch → purple
    #   soil  (B04≈3500, B03≈2800, B02≈2000) → independent stretch → orange
    # With a shared stretch, relative reflectance ratios are preserved,
    # giving natural-looking colours regardless of scene content.
    #
    # CLOUD_DN_THRESH: clouds are ~6000–10000 DN; exclude them from the
    # percentile sample so they don't compress the land into a narrow range.
    CLOUD_DN_THRESH = 6000   # S-2 L2A: soil/sand up to ~5500 is valid
    GAMMA = 1.8              # lift shadows/water (>1 brightens mid-tones)

    # Pool clear-land pixels from ALL three channels to get a single lo/hi
    all_clear: list[np.ndarray] = []
    for ch in channels:
        finite = np.isfinite(ch)
        clear = ch[finite & (ch <= CLOUD_DN_THRESH)]
        if clear.size > 0:
            all_clear.append(clear)

    if all_clear:
        combined = np.concatenate(all_clear)
        lo, hi = np.percentile(combined, [2, 98])
    else:
        # Fallback: use all finite pixels across channels
        all_finite = np.concatenate([ch[np.isfinite(ch)] for ch in channels if np.any(np.isfinite(ch))])
        lo, hi = np.percentile(all_finite, [2, 98]) if all_finite.size > 0 else (0.0, 3000.0)
    if hi <= lo:
        hi = lo + 1.0

    logger.debug("Preview stretch: lo=%.0f  hi=%.0f  (shared across R/G/B)", lo, hi)

    rgb_stretched = []
    for ch in channels:
        finite_mask = np.isfinite(ch)
        if not np.any(finite_mask):
            rgb_stretched.append(np.full((out_h, out_w), 128, dtype=np.uint8))
            continue

        # Stretch with shared lo/hi; clouds clip to white, deep shadow to black
        stretched = np.clip((ch - lo) / (hi - lo), 0.0, 1.0)
        # Gamma > 1 brightens mid-tones (especially dark water/flooded fields)
        stretched = np.power(stretched, 1.0 / GAMMA)
        out = (np.nan_to_num(stretched, nan=0.0) * 255).astype(np.uint8)
        out[~finite_mask] = 0
        rgb_stretched.append(out)

    rgb_array = np.stack(rgb_stretched, axis=-1)  # (H, W, 3)

    # Build alpha channel: transparent where ANY channel is nodata
    valid_mask = np.all(np.stack([np.isfinite(ch) for ch in channels], axis=-1), axis=-1)
    alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
    rgba_array = np.concatenate([rgb_array, alpha[..., np.newaxis]], axis=-1)  # (H, W, 4)

    img = Image.fromarray(rgba_array, mode="RGBA")
    img.save(preview_path, format="PNG", optimize=True)

    logger.info("Preview written: %s  (%d×%d px)", preview_path, out_w, out_h)
    return preview_path
