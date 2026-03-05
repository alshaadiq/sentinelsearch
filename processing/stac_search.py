"""
STAC scene discovery via Microsoft Planetary Computer.

Features
--------
- Searches Sentinel-2 L2A collection
- Filters by AOI bbox, date range, and cloud cover < threshold
- Signs assets for direct raster access
- Caps result at MAX_SCENES scenes, sorted by cloud cover ascending
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import planetary_computer
import pystac
from pystac_client import Client
from shapely.geometry import mapping, shape

from backend.config import settings

logger = logging.getLogger(__name__)

# Sentinel-2 L2A collection on Planetary Computer
COLLECTION = "sentinel-2-l2a"

# SCL classes to mask (cloud shadow, medium cloud, high cloud, cirrus)
CLOUD_SCL_CLASSES = {3, 8, 9, 10}

# All bands needed for the analysis profile
ANALYSIS_BANDS = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12", "SCL"]


def search_sentinel2_scenes(
    aoi_geojson: Dict[str, Any],
    date_start: str,
    date_end: str,
) -> List[pystac.Item]:
    """
    Search Planetary Computer for Sentinel-2 L2A scenes.

    Parameters
    ----------
    aoi_geojson : dict
        GeoJSON geometry (Polygon or MultiPolygon) in EPSG:4326.
    date_start : str
        ISO-8601 date string, e.g. "2024-01-01".
    date_end : str
        ISO-8601 date string, e.g. "2024-06-30".

    Returns
    -------
    list of pystac.Item
        Signed STAC items, sorted by cloud cover, capped at MAX_SCENES.
    """
    # Simplify geometry for STAC query (use bounding box)
    geom = shape(aoi_geojson)
    geom_simplified = geom.simplify(0.001, preserve_topology=True)
    bbox = list(geom_simplified.bounds)  # [west, south, east, north]

    logger.info(
        "STAC search: bbox=%s  date=%s/%s  cloud_cover<%.0f%%",
        bbox,
        date_start,
        date_end,
        settings.cloud_cover_max,
    )

    catalog = Client.open(
        settings.stac_api_url,
        modifier=planetary_computer.sign_inplace,
    )

    search = catalog.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{date_start}/{date_end}",
        query={"eo:cloud_cover": {"lt": settings.cloud_cover_max}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        max_items=settings.max_scenes,
    )

    items = list(search.items())
    logger.info("STAC search returned %d items (cap: %d)", len(items), settings.max_scenes)
    return items
