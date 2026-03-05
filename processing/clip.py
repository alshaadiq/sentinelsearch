"""
AOI clipping utilities for xarray / rioxarray DataArrays.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
from shapely.geometry import shape, mapping
import xarray as xr


def clip_to_aoi(da: xr.DataArray, aoi_geojson: Dict[str, Any]) -> xr.DataArray:
    """
    Clip a rioxarray DataArray to the AOI polygon.

    Parameters
    ----------
    da : xr.DataArray
        Rioxarray-enabled DataArray with spatial dimensions.
    aoi_geojson : dict
        GeoJSON Polygon / MultiPolygon geometry in EPSG:4326.

    Returns
    -------
    xr.DataArray
        Clipped and masked DataArray.
    """
    geom = shape(aoi_geojson)
    # Simplify slightly to reduce vertex count
    geom = geom.simplify(0.0001, preserve_topology=True)

    return da.rio.clip(
        [mapping(geom)],
        crs="EPSG:4326",
        from_disk=True,
        all_touched=True,
        drop=True,
    )
