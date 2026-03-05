"""
Lightweight job-state persistence using local JSON files.

Each job writes a single JSON file to data/jobs/<job_id>.json.
This avoids adding a DB while remaining durable across restarts.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import settings

logger = logging.getLogger(__name__)


def _job_path(job_id: str) -> Path:
    return settings.jobs_dir / f"{job_id}.json"


def write_job_meta(job_id: str, meta: Dict[str, Any]) -> None:
    path = _job_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def read_job_meta(job_id: str) -> Optional[Dict[str, Any]]:
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read job meta %s: %s", job_id, exc)
        return None


def update_progress(
    job_id: str,
    stage: str,
    pct: int,
    message: str = "",
    *,
    status: str = "running",
) -> None:
    """Atomically update job progress + status."""
    meta = read_job_meta(job_id)
    if meta is None:
        logger.warning("update_progress: job %s not found", job_id)
        return
    meta["status"] = status
    meta["progress"] = {"stage": stage, "pct": pct, "message": message}
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_job_meta(job_id, meta)


def mark_succeeded(job_id: str, result: Dict[str, Any]) -> None:
    meta = read_job_meta(job_id)
    if meta is None:
        return
    meta["status"] = "succeeded"
    meta["result"] = result
    meta["progress"] = {"stage": "done", "pct": 100, "message": "Composite ready"}
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_job_meta(job_id, meta)


def mark_failed(job_id: str, error: str) -> None:
    meta = read_job_meta(job_id)
    if meta is None:
        return
    meta["status"] = "failed"
    meta["error"] = error
    meta["progress"] = {"stage": "failed", "pct": 0, "message": error}
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_job_meta(job_id, meta)
