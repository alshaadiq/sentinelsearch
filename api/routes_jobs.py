"""
Job endpoints:
  POST /jobs            – submit composite job
  GET  /jobs/{job_id}   – poll status + progress
  GET  /jobs/{job_id}/result – fetch download URLs
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.schemas import (
    BandInfo,
    CompositeRequest,
    JobProgress,
    JobResultResponse,
    JobStatus,
    JobStatusResponse,
    BAND_DESCRIPTIONS,
)
from backend.config import settings
from workers.celery_app import celery_app
from workers.task_state import read_job_meta, write_job_meta

logger = logging.getLogger(__name__)
router = APIRouter()

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _validate_aoi_area(aoi: dict) -> None:
    """Raise 422 if AOI exceeds MAX_AOI_KM2."""
    import geopandas as gpd
    from shapely.geometry import shape

    geom = shape(aoi)
    gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    gdf_proj = gdf.to_crs("EPSG:6933")  # equal-area
    area_km2 = gdf_proj.geometry.area.iloc[0] / 1e6
    if area_km2 > settings.max_aoi_km2:
        raise HTTPException(
            status_code=422,
            detail=f"AOI area {area_km2:.0f} km² exceeds limit of {settings.max_aoi_km2} km².",
        )


def _validate_date_range(date_start, date_end) -> None:
    delta = (date_end - date_start).days
    if delta > settings.max_date_range_days:
        raise HTTPException(
            status_code=422,
            detail=f"Date range {delta} days exceeds limit of {settings.max_date_range_days} days.",
        )


# ──────────────────────────────────────────────
# POST /jobs
# ──────────────────────────────────────────────

@router.post("", status_code=202, summary="Submit a new composite job")
async def submit_job(req: CompositeRequest) -> JSONResponse:
    # Validate limits
    _validate_aoi_area(req.aoi.model_dump())
    _validate_date_range(req.date_start, req.date_end)

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    meta = {
        "job_id": job_id,
        "status": JobStatus.QUEUED.value,
        "progress": {"stage": "queued", "pct": 0, "message": "Job queued"},
        "created_at": now,
        "updated_at": now,
        "error": None,
        "request": {
            "aoi": req.aoi.model_dump(),
            "date_start": str(req.date_start),
            "date_end": str(req.date_end),
            "output_crs": req.output_crs,
        },
    }
    write_job_meta(job_id, meta)

    # Enqueue Celery task
    celery_app.send_task(
        "workers.tasks_composite.run_composite",
        kwargs={"job_id": job_id},
        queue="composite",
        task_id=job_id,
    )

    logger.info("Submitted job %s", job_id)
    return JSONResponse(status_code=202, content={"job_id": job_id})


# ──────────────────────────────────────────────
# GET /jobs/{job_id}
# ──────────────────────────────────────────────

@router.get("/{job_id}", response_model=JobStatusResponse, summary="Poll job status")
async def get_job_status(job_id: str) -> JobStatusResponse:
    meta = read_job_meta(job_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    return JobStatusResponse(
        job_id=meta["job_id"],
        status=JobStatus(meta["status"]),
        progress=JobProgress(**meta.get("progress", {})),
        created_at=meta["created_at"],
        updated_at=meta["updated_at"],
        error=meta.get("error"),
    )


# ──────────────────────────────────────────────
# GET /jobs/{job_id}/result
# ──────────────────────────────────────────────

@router.get("/{job_id}/result", response_model=JobResultResponse, summary="Fetch job result")
async def get_job_result(job_id: str) -> JobResultResponse:
    meta = read_job_meta(job_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if meta["status"] != JobStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not yet succeeded (current status: {meta['status']}).",
        )

    result = meta.get("result", {})

    # Build band list from result metadata
    band_names: list[str] = result.get("bands", [])
    bands = [
        BandInfo(
            index=i + 1,
            name=b,
            description=BAND_DESCRIPTIONS.get(b, b),
        )
        for i, b in enumerate(band_names)
    ]

    return JobResultResponse(
        job_id=job_id,
        cog_url=result.get("cog_url", ""),
        preview_url=result.get("preview_url", ""),
        bands=bands,
        scene_count=result.get("scene_count", 0),
        crs=result.get("crs", "EPSG:4326"),
        bbox=result.get("bbox", []),
    )
