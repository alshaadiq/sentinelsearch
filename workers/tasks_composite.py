"""
Celery task: run the full Sentinel-2 composite pipeline.

Stages and progress percentages:
  5%  – loading job request
 10%  – STAC search
 20%  – building lazy raster stack
 40%  – cloud masking
 60%  – NDVI + greenest pixel selection
 80%  – exporting COG
 90%  – generating PNG preview
100%  – done
"""
from __future__ import annotations

import logging
import traceback

from celery import Task

from workers.celery_app import celery_app
from workers.task_state import mark_failed, mark_succeeded, read_job_meta, update_progress

logger = logging.getLogger(__name__)


class BaseCompositeTask(Task):
    """Base class with error handling."""

    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: D102
        job_id = kwargs.get("job_id", task_id)
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.error("Job %s failed: %s", job_id, error_msg)
        mark_failed(job_id, error_msg)


@celery_app.task(
    bind=True,
    base=BaseCompositeTask,
    name="workers.tasks_composite.run_composite",
    max_retries=0,  # no retries for long-running raster jobs
    time_limit=3600,  # 1 hour hard limit
    soft_time_limit=3300,
)
def run_composite(self: Task, *, job_id: str) -> dict:
    """Orchestrate the full Sentinel-2 greenest-pixel composite pipeline."""

    # ── 1. Load job metadata ──────────────────────────────────────────
    update_progress(job_id, "loading", 5, "Loading job parameters")
    meta = read_job_meta(job_id)
    if meta is None:
        raise RuntimeError(f"Job metadata not found for {job_id}")

    req = meta["request"]
    aoi_geojson = req["aoi"]
    date_start = req["date_start"]
    date_end = req["date_end"]
    output_crs = req.get("output_crs", None)  # None → keep native UTM CRS

    # ── 2. STAC search ────────────────────────────────────────────────
    update_progress(job_id, "stac_search", 10, "Searching Planetary Computer STAC")
    from processing.stac_search import search_sentinel2_scenes

    items = search_sentinel2_scenes(
        aoi_geojson=aoi_geojson,
        date_start=date_start,
        date_end=date_end,
    )
    if not items:
        raise RuntimeError(
            f"No Sentinel-2 scenes found for the given AOI and date range "
            f"({date_start} → {date_end}) with cloud cover < settings limit."
        )
    logger.info("Job %s: found %d scenes", job_id, len(items))
    update_progress(job_id, "stac_search", 15, f"Found {len(items)} scenes")

    # ── 3. Build lazy stack ───────────────────────────────────────────
    update_progress(job_id, "build_stack", 20, "Building lazy raster stack")
    from processing.composite import build_stack

    stack = build_stack(items=items, aoi_geojson=aoi_geojson)

    # ── 4. BRDF normalisation ─────────────────────────────────────────
    update_progress(job_id, "brdf", 30, "Applying BRDF c-factor normalisation")
    from processing.brdf_correction import brdf_normalize_stack

    stack = brdf_normalize_stack(stack=stack, items=items)

    # ── 5. Cloud mask + NDVI composite ───────────────────────────────
    update_progress(job_id, "composite", 40, "Applying cloud mask and computing composite")
    from processing.composite import compute_greenest_pixel_composite

    composite_ds, used_scenes = compute_greenest_pixel_composite(stack=stack)

    # ── 5. Export COG ─────────────────────────────────────────────────
    update_progress(job_id, "export_cog", 80, "Writing Cloud Optimized GeoTIFF")
    from processing.export_cog import export_cog

    cog_path = export_cog(
        composite_ds=composite_ds,
        job_id=job_id,
        output_crs=output_crs,
    )

    # ── 6. Gap-fill cloud / shadow patches ───────────────────────────
    update_progress(job_id, "gap_fill", 85, "Filling cloud/shadow gaps")
    from processing.gap_fill import fill_composite_gaps

    cog_path = fill_composite_gaps(cog_path=cog_path)

    # ── 7. Generate PNG preview ───────────────────────────────────────
    update_progress(job_id, "preview", 90, "Generating PNG quicklook")
    from processing.export_preview import export_preview

    preview_path = export_preview(cog_path=cog_path, job_id=job_id)

    # ── 7. Compute bbox from exported COG (post-reproject, guaranteed correct) ──
    import rasterio as _rio
    import rioxarray  # noqa: F401
    from pyproj import Transformer

    native_crs = composite_ds.rio.crs
    native_crs_str = str(native_crs) if native_crs else "EPSG:4326"

    # Read bounds directly from the COG that will actually be displayed —
    # this avoids any drift introduced by the UTM→WGS84 reprojection inside export_cog.
    with _rio.open(cog_path) as _src:
        _b = _src.bounds
        _cog_crs = _src.crs
    if _cog_crs and _cog_crs.to_epsg() != 4326:
        _t = Transformer.from_crs(_cog_crs, "EPSG:4326", always_xy=True)
        _w, _s = _t.transform(_b.left, _b.bottom)
        _e, _n = _t.transform(_b.right, _b.top)
        bbox_wgs84 = [_w, _s, _e, _n]
    else:
        bbox_wgs84 = [_b.left, _b.bottom, _b.right, _b.top]

    # Build band name list
    band_names: list[str] = list(composite_ds.data_vars.keys())

    result = {
        "cog_url": f"/data/cogs/{cog_path.name}",
        "preview_url": f"/data/previews/{preview_path.name}",
        "bands": band_names,
        "scene_count": used_scenes,
        "crs": native_crs_str,
        "bbox": bbox_wgs84,
    }

    mark_succeeded(job_id, result)
    logger.info("Job %s completed successfully", job_id)
    return result
