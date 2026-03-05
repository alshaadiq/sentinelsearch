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
from shapely.geometry import shape

logger = logging.getLogger(__name__)

# Analysis bands to fetch (SCL needed for cloud mask)
ANALYSIS_BANDS = [
    "B02", "B03", "B04", "B05", "B06", "B07",
    "B08", "B8A", "B11", "B12", "SCL",
]

# SCL class codes that represent clouds / cloud-related contamination
CLOUD_CLASSES = [3, 8, 9, 10]

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

    stack = stackstac.stack(
        items,
        assets=ANALYSIS_BANDS,
        epsg=4326,
        bounds=(minx, miny, maxx, maxy),
        chunksize=CHUNK_XY,
        dtype="float32",
        fill_value=np.nan,
        resampling=stackstac.Resampling.nearest,
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
    b04 = _band("B04")
    b08 = _band("B08")

    # ── Cloud mask: True = clear ──────────────────────────────────────
    clear = _cloud_mask(scl)  # (time, y, x) bool

    # ── NDVI with cloud masking applied ──────────────────────────────
    ndvi_raw = (b08 - b04) / (b08 + b04 + 1e-9)
    ndvi_masked = ndvi_raw.where(clear)  # NaN where cloud

    # ── Trigger compute for argmax (compact operation) ────────────────
    logger.info("Computing NDVI argmax (greenest pixel selection) …")
    ndvi_np: np.ndarray = ndvi_masked.values  # (time, y, x) – triggers Dask compute

    # argmax ignoring NaN: any-valid fallback to 0 when all NaN
    valid_count = np.sum(~np.isnan(ndvi_np), axis=0)  # (y, x)
    # Replace fully-NaN pixels with 0 so argmax doesn't raise
    ndvi_filled = np.where(np.isnan(ndvi_np), -999.0, ndvi_np)
    t_star = np.nanargmax(ndvi_filled, axis=0).astype(np.int32)  # (y, x)

    # Mask t_star where NO valid observation exists
    no_valid = valid_count == 0

    used_scenes = int(np.unique(t_star[~no_valid]).size)
    logger.info(
        "Greenest pixel selection: %d unique time steps used, %.1f%% pixels have no valid obs",
        used_scenes,
        np.mean(no_valid) * 100,
    )

    # ── Select each band at t* per pixel ─────────────────────────────
    logger.info("Selecting per-pixel best-time values for all bands …")

    # Compute full stack as numpy  (time × band × y × x)
    # Done after clipping so spatial extent is small
    stack_np: np.ndarray = stack.values  # triggers full Dask compute

    ny, nx = t_star.shape
    n_bands = stack_np.shape[1]

    # Fancy index: for each (y, x) select stack_np[t_star[y,x], :, y, x]
    yy, xx = np.mgrid[0:ny, 0:nx]
    selected = stack_np[t_star, :, yy, xx]  # (y, x, band)
    selected = np.transpose(selected, (2, 0, 1))  # (band, y, x)

    # Apply nodata mask where no valid observation existed
    selected[:, no_valid] = np.nan

    # ── Build output xr.Dataset ───────────────────────────────────────
    y_coords = stack.y.values
    x_coords = stack.x.values

    output_vars: dict[str, xr.DataArray] = {}
    for i, band_name in enumerate(ANALYSIS_BANDS):
        da_out = xr.DataArray(
            selected[i],
            dims=["y", "x"],
            coords={"y": y_coords, "x": x_coords},
            name=band_name,
            attrs={"long_name": band_name, "grid_mapping": "spatial_ref"},
        )
        output_vars[band_name] = da_out

    # ── Add NDVI as derived band ──────────────────────────────────────
    b04_final = output_vars["B04"].values.astype(np.float32)
    b08_final = output_vars["B08"].values.astype(np.float32)
    ndvi_final = (b08_final - b04_final) / (b08_final + b04_final + 1e-9)
    ndvi_final[no_valid] = np.nan

    output_vars["NDVI"] = xr.DataArray(
        ndvi_final,
        dims=["y", "x"],
        coords={"y": y_coords, "x": x_coords},
        name="NDVI",
        attrs={"long_name": "NDVI (greenest-pixel composite)"},
    )

    composite_ds = xr.Dataset(output_vars)

    # Attach CRS (Planetary Computer stacks in EPSG:4326 when epsg=4326)
    composite_ds = composite_ds.rio.write_crs("EPSG:4326")
    composite_ds = composite_ds.rio.set_spatial_dims(x_dim="x", y_dim="y")

    logger.info("Composite dataset built: %s", composite_ds)
    return composite_ds, used_scenes
