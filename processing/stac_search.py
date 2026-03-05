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
from shapely.geometry import shape

from backend.config import settings

logger = logging.getLogger(__name__)

COLLECTION = "sentinel-2-l2a"


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
        Signed STAC items, sorted by cloud cover ascending, capped at MAX_SCENES.
    """
    geom = shape(aoi_geojson)
    geom_simplified = geom.simplify(0.001, preserve_topology=True)
    bbox = list(geom_simplified.bounds)  # [west, south, east, north]

    logger.info(
        "STAC search: bbox=%s  date=%s/%s  cloud_cover<%.0f%%  max_items=%d",
        bbox,
        date_start,
        date_end,
        settings.cloud_cover_max,
        settings.max_scenes,
    )

    catalog = Client.open(
        settings.stac_api_url,
        modifier=planetary_computer.sign_inplace,
    )

    # ── Primary search: CQL2-JSON filter (Planetary Computer STAC v1) ─
    # sortby: "+" prefix = ascending
    items: List[pystac.Item] = []
    try:
        search = catalog.search(
            collections=[COLLECTION],
            bbox=bbox,
            datetime=f"{date_start}/{date_end}",
            filter={
                "op": "lte",
                "args": [
                    {"property": "eo:cloud_cover"},
                    settings.cloud_cover_max,
                ],
            },
            filter_lang="cql2-json",
            sortby="+properties.eo:cloud_cover",
            max_items=settings.max_scenes,
        )
        items = list(search.items())
        logger.info("CQL2 search returned %d items", len(items))
    except Exception as exc:
        logger.warning("CQL2 filter search failed (%s), falling back to unfiltered search", exc)

    # ── Fallback: plain search, post-filter cloud cover in Python ─────
    if not items:
        logger.info("Falling back to unfiltered search + Python post-filter")
        search = catalog.search(
            collections=[COLLECTION],
            bbox=bbox,
            datetime=f"{date_start}/{date_end}",
            max_items=settings.max_scenes * 3,  # fetch more to allow filtering
        )
        all_items = list(search.items())
        logger.info("Unfiltered search returned %d total items", len(all_items))

        items = [
            it for it in all_items
            if (it.properties.get("eo:cloud_cover") or 100) <= settings.cloud_cover_max
        ]
        logger.info(
            "After cloud_cover<=%.0f%% filter: %d items remain",
            settings.cloud_cover_max,
            len(items),
        )

    # ── Sort by cloud cover ascending, cap at max_scenes ─────────────
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover") or 100)
    items = items[: settings.max_scenes]

    logger.info("STAC search final: %d scenes  (bbox=%s  %s→%s)", len(items), bbox, date_start, date_end)
    if items:
        cc_values = [round(it.properties.get("eo:cloud_cover") or 0, 1) for it in items[:5]]
        logger.info("Cloud cover of first 5 scenes: %s", cc_values)

    return items
