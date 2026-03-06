"""
STAC scene discovery via Microsoft Planetary Computer.

Features
--------
- Searches Sentinel-2 L2A collection
- Filters by AOI bbox, date range, and cloud cover < threshold
- Signs assets for direct raster access
- Caps result at MAX_SCENES scenes, sorted by cloud cover ascending
- Cloud cover is computed **within the AOI**, not over the full scene tile
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import numpy as np
import planetary_computer
import pystac
from pystac_client import Client
from shapely.geometry import shape

from backend.config import settings

logger = logging.getLogger(__name__)

COLLECTION = "sentinel-2-l2a"

# SCL cloud/shadow classes (must match composite.py)
_CLOUD_SCL_CLASSES = frozenset({3, 7, 8, 9, 10})


def _aoi_cloud_cover(item: pystac.Item, aoi_geom) -> float:
    """
    Estimate cloud cover percentage **within the AOI** for a single STAC item.

    Reads the SCL (Scene Classification Layer) asset at its native 20 m
    resolution, clips to the AOI bounding box, masks pixels outside the AOI
    polygon, then returns 100 * n_cloud_pixels / n_aoi_pixels.

    Falls back to the scene-level eo:cloud_cover metadata if the SCL
    asset cannot be read (e.g. network error).
    """
    fallback = float(item.properties.get("eo:cloud_cover") or 100.0)

    try:
        import rasterio
        import rasterio.mask
        from rasterio.enums import Resampling

        scl_href = None
        if "SCL" in item.assets:
            scl_href = item.assets["SCL"].href
        elif "scl" in item.assets:
            scl_href = item.assets["scl"].href
        if scl_href is None:
            return fallback

        with rasterio.open(scl_href) as src:
            # Overview level ~ 160 m — fast read, still accurate for cloud %
            overview_level = min(2, len(src.overviews(1)) - 1) if src.overviews(1) else 0
            out_shape_factor = 2 ** overview_level
            out_h = max(1, src.height // out_shape_factor)
            out_w = max(1, src.width  // out_shape_factor)

            # Read clipped to AOI bounding box
            aoi_arr, _ = rasterio.mask.mask(
                src,
                [aoi_geom.__geo_interface__],
                crop=True,
                nodata=0,
                out_shape=(1, out_h, out_w),
                resampling=Resampling.nearest,
                all_touched=True,
            )
        scl = aoi_arr[0].astype(np.uint8)  # (H, W)

        # AOI pixels: all non-nodata (nodata=0 is SCL "no data" class)
        aoi_mask = scl > 0
        n_aoi = int(aoi_mask.sum())
        if n_aoi == 0:
            return fallback

        cloud_mask = np.zeros_like(scl, dtype=bool)
        for cls in _CLOUD_SCL_CLASSES:
            cloud_mask |= (scl == cls)

        n_cloud = int((cloud_mask & aoi_mask).sum())
        return round(n_cloud / n_aoi * 100.0, 2)

    except Exception as exc:
        logger.debug("AOI cloud cover read failed for %s: %s — using metadata fallback", item.id, exc)
        return fallback


def search_sentinel2_scenes(
    aoi_geojson: Dict[str, Any],
    date_start: str,
    date_end: str,
    cloud_cover_max: float | None = None,
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
    cloud_cover_max : float or None
        Override the configured cloud cover threshold (0–100 %).
        Pass 100.0 to fetch all scenes regardless of cloud cover.
        None (default) uses settings.cloud_cover_max.

    Returns
    -------
    list of pystac.Item
        Signed STAC items, sorted by AOI cloud cover ascending, capped at MAX_SCENES.
    """
    cc_max = cloud_cover_max if cloud_cover_max is not None else settings.cloud_cover_max
    aoi_geom = shape(aoi_geojson)
    bbox = list(aoi_geom.bounds)  # [west, south, east, north]

    logger.info(
        "STAC search: bbox=%s  date=%s/%s  aoi_cloud_cover<%.0f%%  max_items=%d",
        bbox,
        date_start,
        date_end,
        cc_max,
        settings.max_scenes,
    )

    catalog = Client.open(
        settings.stac_api_url,
        modifier=planetary_computer.sign_inplace,
    )

    # ── Broad search with loose scene-level pre-filter ─────────────────
    # Use a generous pre-filter to get candidate scenes, then apply accurate
    # AOI-clipped cloud cover as the real filter below.
    # When cc_max==100 (cloud_patching), skip the STAC-level filter entirely.
    pre_filter_cc = min(100.0, cc_max * 2.5)

    items_raw: List[pystac.Item] = []
    try:
        search_kwargs: dict = dict(
            collections=[COLLECTION],
            bbox=bbox,
            datetime=f"{date_start}/{date_end}",
            sortby="+properties.eo:cloud_cover",
            max_items=settings.max_scenes * 3,
        )
        if pre_filter_cc < 100.0:
            search_kwargs["filter"] = {
                "op": "lte",
                "args": [{"property": "eo:cloud_cover"}, pre_filter_cc],
            }
            search_kwargs["filter_lang"] = "cql2-json"
        search = catalog.search(**search_kwargs)
        items_raw = list(search.items())
        logger.info("CQL2 pre-filter search returned %d candidate items", len(items_raw))
    except Exception as exc:
        logger.warning("CQL2 search failed (%s), falling back to unfiltered search", exc)
        search = catalog.search(
            collections=[COLLECTION],
            bbox=bbox,
            datetime=f"{date_start}/{date_end}",
            max_items=settings.max_scenes * 3,
        )
        items_raw = list(search.items())
        logger.info("Unfiltered fallback returned %d items", len(items_raw))

    # ── Compute AOI-clipped cloud cover for each candidate ─────────────
    logger.info("Computing AOI cloud cover for %d candidates…", len(items_raw))
    scored: list[tuple[float, pystac.Item]] = []
    for item in items_raw:
        aoi_cc = _aoi_cloud_cover(item, aoi_geom)
        scored.append((aoi_cc, item))
        # Attach as custom property so downstream code can log/use it
        item.properties["aoi_cloud_cover"] = aoi_cc

    # ── Filter, sort, cap ─────────────────────────────────────────────
    filtered = [(cc, it) for cc, it in scored if cc <= cc_max]
    filtered.sort(key=lambda x: x[0])
    filtered = filtered[: settings.max_scenes]

    items: List[pystac.Item] = [it for _, it in filtered]

    logger.info(
        "After AOI cloud cover <=%.0f%% filter: %d/%d items kept",
        cc_max, len(items), len(items_raw),
    )
    if items:
        cc_values = [
            f"{it.properties.get('aoi_cloud_cover', it.properties.get('eo:cloud_cover', '?')):.1f}%"
            for it in items[:5]
        ]
        logger.info("AOI cloud cover of first 5 scenes: %s", cc_values)

    return items

