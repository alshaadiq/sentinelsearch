"""
SentinelSearch – FastAPI application entry point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# App lifecycle
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting SentinelSearch API …")
    settings.ensure_dirs()
    logger.info("Storage dirs ready: %s", settings.data_dir)
    yield
    logger.info("SentinelSearch API shutting down.")


# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────
app = FastAPI(
    title="SentinelSearch",
    description="Cloud-free Sentinel-2 composite generation platform",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# Routers
# ──────────────────────────────────────────────
from api.routes_health import router as health_router  # noqa: E402
from api.routes_jobs import router as jobs_router  # noqa: E402

app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(jobs_router, prefix="/jobs", tags=["jobs"])

# ──────────────────────────────────────────────
# Static files (serve generated COGs & previews)
# ──────────────────────────────────────────────
_data_dir = settings.data_dir
if _data_dir.exists():
    app.mount("/data", StaticFiles(directory=str(_data_dir)), name="data")
