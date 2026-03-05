"""Health-check endpoint."""
from __future__ import annotations

import redis as _redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.config import settings

router = APIRouter()


@router.get("", summary="API health check")
async def health() -> JSONResponse:
    status: dict = {"api": "ok", "redis": "unknown"}

    try:
        r = _redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        status["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["redis"] = f"error: {exc}"

    http_status = 200 if all(v == "ok" for v in status.values()) else 503
    return JSONResponse(status_code=http_status, content=status)
