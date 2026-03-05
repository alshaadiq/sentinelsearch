"""
Export the composite xr.Dataset as a Cloud Optimised GeoTIFF (COG).

Profile
-------
- Format       : GeoTIFF
- Tiling       : 512 × 512 internal tiles
- Compression  : DEFLATE (level 6) + predictor 2
- Overviews    : 2 4 8 16 32
- CRS          : configurable (default EPSG:4326 or UTM auto-detect)
- No-data      : NaN → stored as float32 nodata

Band order (written to COG)
---------------------------
B02, B03, B04, B05, B06, B07, B08, B8A, B11, B12, SCL, NDVI
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
import rasterio.crs
import rasterio.transform
from rasterio.enums import Resampling as RIOResampling
from rasterio.shutil import copy as rio_copy
import xarray as xr

from backend.config import settings

logger = logging.getLogger(__name__)

# Canonical band order for the output COG
OUTPUT_BAND_ORDER = [
    "B02", "B03", "B04", "B05", "B06",
    "B07", "B08", "B8A", "B11", "B12",
    "SCL", "NDVI",
]

COG_BLOCK_SIZE = 512
OVERVIEW_FACTORS = [2, 4, 8, 16, 32]


def export_cog(
    composite_ds: xr.Dataset,
    job_id: str,
    output_crs: str | None = None,
) -> Path:
    """
    Write composite dataset to a Cloud Optimized GeoTIFF.

    Parameters
    ----------
    composite_ds : xr.Dataset
        Output from :func:`processing.composite.compute_greenest_pixel_composite`.
    job_id : str
        Used to name the output file.
    output_crs : str or None
        Target CRS e.g. "EPSG:4326" or "EPSG:32632".
        None (default) keeps the native CRS of the composite.

    Returns
    -------
    Path
        Absolute path to the written COG file.
    """
    import rioxarray  # noqa: F401

    cog_path = settings.cogs_dir / f"{job_id}.tif"
    tmp_path = settings.cogs_dir / f"{job_id}_tmp.tif"

    # Reproject composite if needed
    ds = composite_ds
    src_crs = ds.rio.crs
    if src_crs is None:
        src_crs_str = "EPSG:4326"
        ds = ds.rio.write_crs(src_crs_str)
    else:
        src_crs_str = str(src_crs)

    effective_crs = output_crs or src_crs_str
    if output_crs and output_crs.upper() != src_crs_str.upper():
        logger.info("Reprojecting composite from %s to %s", src_crs_str, output_crs)
        # write_nodata is only on DataArray.rio in rioxarray ≤0.15.x, not Dataset.rio
        ds = xr.Dataset(
            {var: ds[var].rio.write_nodata(np.nan, encoded=False) for var in ds.data_vars},
            attrs=ds.attrs,
        )
        ds = ds.rio.reproject(output_crs, nodata=np.nan)

    # Collect bands in canonical order (only include those present)
    band_names = [b for b in OUTPUT_BAND_ORDER if b in ds.data_vars]

    def _to_float32(da: xr.DataArray) -> np.ndarray:
        """Extract a plain float32 numpy array, converting masked-array fill-values to NaN."""
        arr = da.values
        if isinstance(arr, np.ma.MaskedArray):
            arr = arr.filled(np.nan)
        return np.asarray(arr, dtype=np.float32)

    arrays = [_to_float32(ds[b]) for b in band_names]
    data = np.stack(arrays, axis=0)  # (bands, height, width)

    height, width = data.shape[1], data.shape[2]

    # Build affine transform from xarray coordinates
    first_var = ds[band_names[0]]
    x_coords = first_var.x.values
    y_coords = first_var.y.values
    res_x = float(x_coords[1] - x_coords[0]) if len(x_coords) > 1 else 1e-4
    res_y = float(y_coords[1] - y_coords[0]) if len(y_coords) > 1 else -1e-4

    transform = rasterio.transform.from_origin(
        west=float(x_coords[0]) - res_x / 2,
        north=float(y_coords[0]) - res_y / 2,
        xsize=abs(res_x),
        ysize=abs(res_y),
    )

    # ── Write temporary GeoTIFF ───────────────────────────────────────
    logger.info("Writing temp GeoTIFF: %d bands %d×%d", len(band_names), height, width)
    with rasterio.open(
        tmp_path,
        mode="w",
        driver="GTiff",
        count=len(band_names),
        dtype="float32",
        width=width,
        height=height,
        crs=rasterio.crs.CRS.from_string(effective_crs),
        transform=transform,
        nodata=np.nan,
    ) as dst:
        dst.write(data)
        for i, name in enumerate(band_names, start=1):
            dst.update_tags(i, name=name)

        # ── Build overviews ───────────────────────────────────────────
        dst.build_overviews(OVERVIEW_FACTORS, RIOResampling.average)
        dst.update_tags(ns="rio_overview", resampling="average")

    # ── Copy to COG format ────────────────────────────────────────────
    logger.info("Converting to COG: %s", cog_path)
    rio_copy(
        tmp_path,
        cog_path,
        driver="GTiff",
        copy_src_overviews=True,
        compress="DEFLATE",
        predictor=2,
        tiled=True,
        blockxsize=COG_BLOCK_SIZE,
        blockysize=COG_BLOCK_SIZE,
        nodata=np.nan,
    )

    tmp_path.unlink(missing_ok=True)
    logger.info("COG written: %s  (%.1f MB)", cog_path, cog_path.stat().st_size / 1e6)
    return cog_path
