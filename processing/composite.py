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

# Analysis bands to fetch (SCL needed for cloud mask)
ANALYSIS_BANDS = [
    "B02", "B03", "B04", "B05", "B06", "B07",
    "B08", "B8A", "B11", "B12", "SCL",
]

# SCL class codes that represent clouds / cloud-related contamination.
# Class 7 (unclassified) is included because cloud edges often land here.
CLOUD_CLASSES = [3, 7, 8, 9, 10]

# Morphological buffer applied to the cloud mask (pixels).
# Dilating the cloud region by this amount catches the hazy transition
# zone around cloud edges that SCL under-classifies.
CLOUD_BUFFER_PX = 12

# Radiometric haze filter thresholds (Sentinel-2 L2A DN, scale 0–10000).
# Applied AFTER SCL masking to catch thin cirrus / aerosol haze that SCL misses.
# B02 (blue) is strongly elevated by scattering; clear land rarely exceeds 2000 DN.
# B02/B04 (blue/red ratio) > HAZE_BR_RATIO indicates aerosol-dominated scenes.
BLUE_HAZE_DN    = 2000   # B02 > this → hazy pixel
HAZE_BR_RATIO   = 1.6    # B02/B04 > this AND B02 > 1200 → aerosol-haze pixel

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

    stack = stackstac.stack(
        items,
        assets=ANALYSIS_BANDS,
        epsg=epsg_val,
        bounds_latlon=(minx, miny, maxx, maxy),  # auto-reprojects WGS84 bbox to UTM
        resolution=10,   # 10 m — native S-2 optical resolution (20 m bands upsampled)
        chunksize=CHUNK_XY,
        dtype="float32",
        fill_value=np.float32("nan"),
        resampling=Resampling.nearest,
        rescale=False,  # keep raw DN values; Sentinel-2 L2A scale=1 anyway
    )

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
    haze_mask = haze_blue | (haze_ratio & (b02 > 1200))    # (time, y, x) bool
    clear = clear & ~haze_mask
    logger.info("Radiometric haze filter applied (B02>%d or B02/B04>%.1f)",
                BLUE_HAZE_DN, HAZE_BR_RATIO)

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
