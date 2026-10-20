"""Integration queues retain the former parsers broker and task identities."""

import os

from celery import Celery
from celery.schedules import crontab

from core.celery_settings import scope_broker_settings

app = Celery(
    "integrations",
    broker=os.getenv("INTEGRATION_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("INTEGRATION_RESULT_BACKEND", "redis://redis:6379/0"),
    include=["tasks.integrations", "tasks.ingestion"],
)
app.conf.update(
    timezone="UTC",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    result_expires=3600,
    task_routes={"tasks.integrations.execute": {"queue": "scraping"}},
    beat_schedule={
        "fetch-api-data-every-hour": {
            "task": "tasks.tasks.fetch_api_data",
            "schedule": crontab(minute="*/60"),
        },
        "delete-vehicles-evry-hour-at-0:15": {
            "task": "tasks.tasks.delete_vehicle",
            "schedule": crontab(minute=5),
        },
    },
)
scope_broker_settings(app)
