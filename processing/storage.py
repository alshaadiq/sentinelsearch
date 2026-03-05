"""
Storage helpers – path generation and result manifest writing.
"""
from __future__ import annotations

from pathlib import Path

from backend.config import settings


def cog_path_for_job(job_id: str) -> Path:
    return settings.cogs_dir / f"{job_id}.tif"


def preview_path_for_job(job_id: str) -> Path:
    return settings.previews_dir / f"{job_id}.png"


def cog_url_for_job(job_id: str) -> str:
    return f"/data/cogs/{job_id}.tif"


def preview_url_for_job(job_id: str) -> str:
    return f"/data/previews/{job_id}.png"
