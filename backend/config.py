"""
Central settings loaded from environment / .env file.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # STAC
    stac_api_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Storage
    data_dir: Path = Path("/app/data")

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cogs_dir(self) -> Path:
        return self.data_dir / "cogs"

    @property
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

    # CORS
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Job safety limits
    max_scenes: int = 30
    max_aoi_km2: float = 2500.0
    max_date_range_days: int = 180
    cloud_cover_max: float = 60.0

    # Logging
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        """Create storage directories if they don't exist."""
        for d in [self.jobs_dir, self.cogs_dir, self.previews_dir]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
