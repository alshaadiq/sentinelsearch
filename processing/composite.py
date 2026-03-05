"""
Sentinel-2 Greenest Pixel Composite pipeline.

Algorithm
---------
1. Lazy-load all analysis bands via stackstac (time × band × y × x).
2. Clip to AOI bounding box early (reduces data fetched).
3. Cloud-mask per pixel using SCL classes 3, 8, 9, 10.
4. Compute NDVI  =  (B08 − B04) / (B08 + B04)  only for clear pixels.
5. t* = argmax(NDVI, axis=time) per pixel  →  shared scene index.
6. For **every** band, pick the pixel value at its own spatial t*.
   This preserves spectral consistency (all bands from same acquisition).
7. Add NDVI as a derived output band (at same t*).
8. Return an xr.Dataset where each variable is one output band.

Band outputs
-----------
B02, B03, B04, B05, B06, B07, B08, B8A, B11, B12, SCL, NDVI
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import dask.array as da
import numpy as np
import pandas as pd
import planetary_computer
import pystac
import rioxarray  # noqa: F401 – activates .rio accessor
import stackstac
import xarray as xr
from rasterio.enums import Resampling
from shapely.geometry import shape

logger = logging.getLogger(__name__)

# Analysis bands to fetch (SCL loaded separately with nearest-neighbor)
ANALYSIS_BANDS = [
    "B02", "B03", "B04", "B05", "B06", "B07",
    "B08", "B8A", "B11", "B12", "SCL",
]
# Reflectance-only bands — loaded with bilinear resampling for sub-pixel accuracy
_REFLECTANCE_BANDS = [b for b in ANALYSIS_BANDS if b != "SCL"]

# SCL class codes that represent clouds / cloud-related contamination.
# Class 7 (unclassified) is included because cloud edges often land here.
CLOUD_CLASSES = [3, 7, 8, 9, 10]

# Morphological buffer applied to the cloud mask (pixels).
# Dilating the cloud region by this amount catches the hazy transition
# zone around cloud edges that SCL under-classifies.
CLOUD_BUFFER_PX = 12

# Radiometric haze filter thresholds (Sentinel-2 L2A DN, scale 0–10000).
# Applied AFTER SCL masking to catch thin cirrus / aerosol haze that SCL misses.
# B02 (blue) is strongly elevated by scattering; clear land typically < 2000 DN.
# B02/B04 (blue/red ratio) > HAZE_BR_RATIO indicates aerosol-dominated pixels.
BLUE_HAZE_DN    = 2000   # B02 > this → hazy pixel
HAZE_BR_RATIO   = 1.6    # B02/B04 > this AND B02 > 1200 → aerosol-haze pixel

# Scene-level haze rejection — ADAPTIVE.
# Uses the best-quarter of scenes as a reference rather than a fixed DN
# so it works equally well over dark forest, urban, and arid AOIs.
# Reject scenes whose B02 median exceeds the 25th-percentile reference by
# SCENE_HAZE_RELATIVE_FACTOR *or* SCENE_HAZE_MIN_MARGIN_DN, whichever is larger.
# Safety: if the filter would leave fewer than SCENE_HAZE_MIN_KEEP scenes,
# it is skipped entirely (avoids producing an empty / fully-gap-filled composite).
SCENE_HAZE_RELATIVE_FACTOR = 1.6   # reject if median B02 > 1.6 × p25 reference
SCENE_HAZE_MIN_MARGIN_DN   = 500   # floor: always ≥ 500 DN above reference
SCENE_HAZE_MIN_KEEP        = 3     # never reject below this many scenes

# Dask chunk size along spatial axes (pixels)
CHUNK_XY = 512


def build_stack(
    items: List[pystac.Item],
    aoi_geojson: Dict[str, Any],
) -> xr.DataArray:
    """
    Build a lazy (time × band × y × x) DataArray clipped to the AOI bbox.

    Parameters
    ----------
    items : list of pystac.Item
        Signed STAC items from Planetary Computer.
    aoi_geojson : dict
        GeoJSON geometry used to compute crop bbox.

    Returns
    -------
    xr.DataArray
        Dask-backed DataArray; nothing downloaded yet.
    """
    geom = shape(aoi_geojson)
    minx, miny, maxx, maxy = geom.bounds

    # Determine UTM EPSG from first item (Sentinel-2 STAC items carry proj:epsg)
    epsg_val: int = 32632  # safe default (UTM zone 32 N, central Europe)
    if items:
        epsg_val = items[0].properties.get("proj:epsg") or epsg_val
        # Fallback: infer from MGRS tile code, e.g. "32TPQ" → zone 32 → EPSG:32632
        if not items[0].properties.get("proj:epsg"):
            mgrs = items[0].properties.get("s2:mgrs_tile", "")
            zone_digits = "".join(c for c in mgrs[:3] if c.isdigit())
            if zone_digits:
                epsg_val = 32600 + int(zone_digits)
    logger.info("Using EPSG:%d for stackstac (from first item)", epsg_val)

    # Reflectance bands: bilinear resampling preserves sub-pixel accuracy when
    # warping scenes onto the common output grid (nearest-neighbor creates
    # blocky stepping artefacts on continuous reflectance values).
    stack_ref = stackstac.stack(
        items,
        assets=_REFLECTANCE_BANDS,
        epsg=epsg_val,
        bounds_latlon=(minx, miny, maxx, maxy),
        resolution=10,
        chunksize=CHUNK_XY,
        dtype="float32",
        fill_value=np.float32("nan"),
        resampling=Resampling.bilinear,
        rescale=False,
    )

    # SCL is a classification layer: nearest-neighbor must be used so integer
    # class codes are never blended into meaningless fractional values.
    stack_scl = stackstac.stack(
        items,
        assets=["SCL"],
        epsg=epsg_val,
        bounds_latlon=(minx, miny, maxx, maxy),
        resolution=10,
        chunksize=CHUNK_XY,
        dtype="float32",
        fill_value=np.float32("nan"),
        resampling=Resampling.nearest,
        rescale=False,
    )

    # Merge back into a single (time × band × y × x) DataArray.
    # stackstac attaches per-band STAC metadata as non-dimension coordinates
    # (common_name, center_wavelength, etc.).  SCL lacks most of these, so
    # xr.concat raises ValueError unless we strip them first.
    _STAC_BAND_COORDS = [
        "common_name", "center_wavelength", "full_width_half_max",
        "gsd", "title", "epsg", "proj:shape", "proj:transform",
    ]
    def _strip_band_meta(da: xr.DataArray) -> xr.DataArray:
        drop = [c for c in _STAC_BAND_COORDS if c in da.coords]
        return da.drop_vars(drop) if drop else da

    stack = xr.concat([_strip_band_meta(stack_ref), _strip_band_meta(stack_scl)], dim="band")

    logger.info(
        "Stack shape: %s  bands: %s  chunks: %s",
        stack.shape,
        list(stack.band.values),
        stack.chunks,
    )
    return stack


def _cloud_mask(scl: xr.DataArray) -> xr.DataArray:
    """
    Return boolean mask: True = clear, False = cloudy.

    SCL is expected as a (time, y, x) DataArray.
    """
    mask = xr.ones_like(scl, dtype=bool)
    for cls in CLOUD_CLASSES:
        mask = mask & (scl != cls)
    return mask  # True where pixel is clear


def compute_greenest_pixel_composite(
    stack: xr.DataArray,
) -> Tuple[xr.Dataset, int]:
    """
    Compute the cloud-free greenest-pixel composite.

    Parameters
    ----------
    stack : xr.DataArray
        (time × band × y × x) from :func:`build_stack`.

    Returns
    -------
    composite_ds : xr.Dataset
        Dataset with one variable per output band (y × x).
    used_scenes : int
        Number of scenes that contributed valid pixels.
    """
    n_times = stack.sizes["time"]

    # ── Extract individual band slices (all still lazy) ──────────────
    def _band(name: str) -> xr.DataArray:
        return stack.sel(band=name).drop_vars("band")

    scl = _band("SCL")
    b02 = _band("B02")
    b04 = _band("B04")
    b08 = _band("B08")

    # ── Cloud mask: True = clear ──────────────────────────────────────
    clear = _cloud_mask(scl)  # (time, y, x) bool

    # Dilate cloud region by CLOUD_BUFFER_PX to catch hazy cloud edges.
    # binary_erosion of the clear mask ≡ binary_dilation of the cloud mask.
    # Applied per time-slice via dask map_blocks (remains lazy).
    if CLOUD_BUFFER_PX > 0:
        _struct2d = np.ones(
            (CLOUD_BUFFER_PX * 2 + 1, CLOUD_BUFFER_PX * 2 + 1), dtype=bool
        )

        def _erode_clear(block: np.ndarray) -> np.ndarray:
            from scipy.ndimage import binary_erosion
            out = np.empty_like(block)
            for t in range(block.shape[0]):
                out[t] = binary_erosion(
                    block[t], structure=_struct2d, border_value=True
                )
            return out

        _dilated_data = clear.data.map_blocks(
            _erode_clear, dtype=bool,
        )
        clear = xr.DataArray(_dilated_data, coords=clear.coords, dims=clear.dims)
        logger.info("Cloud mask dilated by %d px (SCL classes masked: %s)",
                    CLOUD_BUFFER_PX, CLOUD_CLASSES)

    # ── Radiometric haze filter (catches what SCL misses) ─────────────────
    # Thin cirrus and aerosol haze lift the blue band well above clear-land levels.
    # This filter operates on actual reflectance values, not SCL labels.
    haze_blue = b02 > BLUE_HAZE_DN                         # absolute blue elevation
    haze_ratio = (b02 / (b04 + 1e-6)) > HAZE_BR_RATIO     # blue/red excess → scattering
    haze_mask = haze_blue | (haze_ratio & (b02 > 1200))
    clear = clear & ~haze_mask
    logger.info("Radiometric pixel-haze filter: B02>%d or B02/B04>%.1f",
                BLUE_HAZE_DN, HAZE_BR_RATIO)

    # ── Scene-level haze rejection ───────────────────────────────────────
    # Regional smoke / aerosol events make every pixel in a scene subtly hazy at
    # 1000–1400 DN — below the per-pixel threshold, but still clearly hazy visually.
    # Compute median B02 over SCL-clear pixels per scene (cheap: (time,) shape).
    # Scenes whose median exceeds SCENE_HAZE_B02_MEDIAN are flagged and their
    # pixels never win the NDVI argmax.
    # Rebuild a raw (pre-dilation) SCL mask for scene scoring to avoid
    # geometry-aware erosion bias on the median.
    _scl_clear_raw = xr.ones_like(scl, dtype=bool)
    for _cls in CLOUD_CLASSES:
        _scl_clear_raw = _scl_clear_raw & (scl != _cls)

    # median B02 per scene over all SCL-clear pixels → shape (time,)
    scene_b02_median = (
        b02.where(_scl_clear_raw)  # NaN on cloudy pixels
        .median(dim=["y", "x"])    # spatial reduction → (time,)  -- lazy
        .compute()                  # only (n_scenes,) values, < 1 s
        .values.astype(np.float32)
    )
    # Adaptive threshold: 25th-percentile of scene medians is the "clean" reference.
    # Reject scenes that are SCENE_HAZE_RELATIVE_FACTOR above that reference,
    # with a minimum margin of SCENE_HAZE_MIN_MARGIN_DN.
    valid_medians_mask = ~np.isnan(scene_b02_median)
    if valid_medians_mask.sum() >= SCENE_HAZE_MIN_KEEP:
        ref_b02 = float(np.percentile(scene_b02_median[valid_medians_mask], 25))
        adaptive_thresh = max(
            ref_b02 * SCENE_HAZE_RELATIVE_FACTOR,
            ref_b02 + SCENE_HAZE_MIN_MARGIN_DN,
        )
    else:
        ref_b02 = float(np.nanmin(scene_b02_median)) if valid_medians_mask.any() else 99999.0
        adaptive_thresh = ref_b02 + SCENE_HAZE_MIN_MARGIN_DN
    hazy_scene_flags = np.isnan(scene_b02_median) | (scene_b02_median > adaptive_thresh)
    # Safety: never leave fewer than SCENE_HAZE_MIN_KEEP scenes
    n_remaining = int((~hazy_scene_flags).sum())
    if n_remaining < SCENE_HAZE_MIN_KEEP:
        hazy_scene_flags = np.zeros(n_times, dtype=bool)
        logger.warning(
            "Scene haze filter would leave only %d/%d scenes — filter skipped. "
            "Scene medians: %s",
            n_remaining, n_times, np.round(scene_b02_median, 0).tolist(),
        )
    n_hazy_scenes = int(hazy_scene_flags.sum())
    if n_hazy_scenes > 0:
        logger.info(
            "Scene-level haze: %d/%d scenes rejected (adaptive thresh=%.0f DN, "
            "p25 ref=%.0f DN). Scene medians: %s",
            n_hazy_scenes, n_times, adaptive_thresh, ref_b02,
            np.round(scene_b02_median, 0).tolist(),
        )
        hazy_scene_da = xr.DataArray(
            hazy_scene_flags,
            dims=["time"],
            coords={"time": stack.coords["time"]},
        )
        clear = clear & ~hazy_scene_da
    else:
        logger.info(
            "Scene-level haze: all %d scenes passed (adaptive thresh=%.0f DN, "
            "p25 ref=%.0f DN, max median=%.0f DN).",
            n_times, adaptive_thresh, ref_b02, float(np.nanmax(scene_b02_median)),
        )

    # ── NDVI with cloud masking applied ──────────────────────────────
    ndvi_raw = (b08 - b04) / (b08 + b04 + 1e-9)
    ndvi_masked = ndvi_raw.where(clear)  # NaN where cloud

    # ── NDVI argmax via dask – processes one spatial chunk at a time ────
    # Each chunk: CHUNK_XY × CHUNK_XY × n_times × 4 B ≈ 32 MB  (never loads full array)
    logger.info("Computing NDVI argmax (greenest pixel selection) …")

    # Fill cloud-masked NaN with -999 so masked pixels never win argmax
    ndvi_for_argmax = ndvi_masked.fillna(-999.0)
    # any_valid: True where ≥1 cloud-free observation exists for this pixel
    any_valid = clear.any(dim="time")  # (y, x) lazy

    t_star = ndvi_for_argmax.argmax(dim="time").compute().values.astype(np.int32)  # (y, x)
    no_valid = (~any_valid.compute().values)  # (y, x) bool

    # Fallback: pixels with zero cloud-free obs → use unconstrained best pixel
    # (preserves full AOI coverage; best-available rather than blank)
    if no_valid.any():
        pct = float(np.mean(no_valid) * 100)
        logger.info("%.1f%% pixels lack cloud-free obs — applying unconstrained fallback", pct)
        t_fallback = ndvi_raw.fillna(-999.0).argmax(dim="time").compute().values.astype(np.int32)
        t_star[no_valid] = t_fallback[no_valid]
        del t_fallback

    used_scenes = int(np.unique(t_star).size)
    logger.info("Greenest pixel selection: %d unique time steps used", used_scenes)

    # ── Select each band at t* per pixel — scene-by-scene ────────────
    # For each unique scene index t: load ONE time slice (all 11 bands ≈ 250 MB),
    # then copy the pixels where t_star == t into the output array.
    # Peak memory: ~500 MB (output + one scene).  Never loads all 30 scenes at once.
    logger.info("Selecting per-pixel best-time values — iterating %d scenes …", n_times)
    ny, nx = t_star.shape
    n_bands_out = len(ANALYSIS_BANDS)
    output_np = np.full((n_bands_out, ny, nx), np.nan, dtype=np.float32)

    for t_idx in range(n_times):
        pixel_mask = t_star == t_idx
        if not np.any(pixel_mask):
            continue
        # Load this single time step (all bands). shape: (band, y, x) ≈ 250 MB
        scene_data = stack.isel(time=t_idx).compute().values  # → numpy (band, y, x)
        for b_idx in range(n_bands_out):
            output_np[b_idx][pixel_mask] = scene_data[b_idx][pixel_mask]
        del scene_data
        logger.debug("Applied scene t=%d, covering %d px", t_idx, int(pixel_mask.sum()))

    y_coords = stack.y.values
    x_coords = stack.x.values
    output_vars: dict[str, xr.DataArray] = {}
    for b_idx, band_name in enumerate(ANALYSIS_BANDS):
        output_vars[band_name] = xr.DataArray(
            output_np[b_idx],
            dims=["y", "x"],
            coords={"y": y_coords, "x": x_coords},
            name=band_name,
            attrs={"long_name": band_name, "grid_mapping": "spatial_ref"},
        )
    del output_np

    # ── Add NDVI as derived band ──────────────────────────────────────
    b04_final = output_vars["B04"].values.astype(np.float32)
    b08_final = output_vars["B08"].values.astype(np.float32)
    ndvi_final = (b08_final - b04_final) / (b08_final + b04_final + 1e-9)
    # no_valid pixels now have fallback data; only blank if truly outside S2 coverage
    # (in that case the scene_data values themselves will already be NaN)

    output_vars["NDVI"] = xr.DataArray(
        ndvi_final,
        dims=["y", "x"],
        coords={"y": y_coords, "x": x_coords},
        name="NDVI",
        attrs={"long_name": "NDVI (greenest-pixel composite)"},
    )

    composite_ds = xr.Dataset(output_vars)

    # Attach native UTM CRS (whatever stackstac chose for the items)
    native_crs = stack.rio.crs
    if native_crs is not None:
        composite_ds = composite_ds.rio.write_crs(native_crs)
    composite_ds = composite_ds.rio.set_spatial_dims(x_dim="x", y_dim="y")

    logger.info("Composite dataset built: %s  CRS=%s", composite_ds, native_crs)
    return composite_ds, used_scenes
