"""
Celery worker configuration.
"""
import os
from celery import Celery
from src.app.config import settings

# Determine broker URL
# Use the one provided by Render if available, else default/local
BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "worker",
    broker=BROKER_URL,
    backend=BROKER_URL,
    include=["src.app.tasks"]  # We will create this file next
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)
