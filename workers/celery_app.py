"""
Celery application factory.
Connects to Redis as both broker and result backend.
"""
from __future__ import annotations

from celery import Celery

from backend.config import settings

celery_app = Celery(
    "sentinelsearch",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["workers.tasks_composite"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # one job at a time per worker
    task_routes={"workers.tasks_composite.run_composite": {"queue": "composite"}},
    task_default_queue="composite",
    result_expires=86400,  # 24 h
)
