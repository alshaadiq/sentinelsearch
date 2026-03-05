"""
Pydantic request / response models for the Jobs API.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ──────────────────────────────────────────────
# GeoJSON helpers (lightweight, no GeoPandas dependency at schema level)
# ──────────────────────────────────────────────

class GeoJSONGeometry(BaseModel):
    type: str
    coordinates: Any

    @field_validator("type")
    @classmethod
    def check_type(cls, v: str) -> str:
        allowed = {"Polygon", "MultiPolygon"}
        if v not in allowed:
            raise ValueError(f"AOI geometry type must be one of {allowed}, got '{v}'")
        return v


# ──────────────────────────────────────────────
# Request
# ──────────────────────────────────────────────

class CompositeRequest(BaseModel):
    """Body sent by the frontend to submit a new composite job."""

    aoi: GeoJSONGeometry = Field(
        ...,
        description="Area of Interest as a GeoJSON Polygon or MultiPolygon (EPSG:4326).",
    )
    date_start: date = Field(..., description="Start of the search window (YYYY-MM-DD).")
    date_end: date = Field(..., description="End of the search window (YYYY-MM-DD).")
    output_crs: str = Field(
        "EPSG:4326",
        description="Output CRS for the Cloud Optimized GeoTIFF.",
    )

    @model_validator(mode="after")
    def validate_date_order(self) -> "CompositeRequest":
        if self.date_end <= self.date_start:
            raise ValueError("date_end must be after date_start.")
        return self


# ──────────────────────────────────────────────
# Job status
# ──────────────────────────────────────────────

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobProgress(BaseModel):
    stage: str = ""
    pct: int = Field(0, ge=0, le=100)
    message: str = ""


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: JobProgress
    created_at: str
    updated_at: str
    error: Optional[str] = None


# ──────────────────────────────────────────────
# Job result
# ──────────────────────────────────────────────

BAND_DESCRIPTIONS: Dict[str, str] = {
    "B02": "Blue (490 nm)",
    "B03": "Green (560 nm)",
    "B04": "Red (665 nm)",
    "B05": "Red Edge 1 (705 nm)",
    "B06": "Red Edge 2 (740 nm)",
    "B07": "Red Edge 3 (783 nm)",
    "B08": "NIR (842 nm)",
    "B8A": "Narrow NIR (865 nm)",
    "B11": "SWIR 1 (1610 nm)",
    "B12": "SWIR 2 (2190 nm)",
    "SCL": "Scene Classification Layer",
    "NDVI": "Normalized Difference Vegetation Index",
}


class BandInfo(BaseModel):
    index: int
    name: str
    description: str


class JobResultResponse(BaseModel):
    job_id: str
    cog_url: str = Field(..., description="URL to download the Cloud Optimized GeoTIFF.")
    preview_url: str = Field(..., description="URL to the PNG quicklook.")
    bands: List[BandInfo] = Field(
        default_factory=list,
        description="Band order in the COG with descriptions.",
    )
    scene_count: int = Field(..., description="Number of valid scenes used.")
    crs: str
    bbox: List[float] = Field(..., description="[west, south, east, north] in EPSG:4326.")
