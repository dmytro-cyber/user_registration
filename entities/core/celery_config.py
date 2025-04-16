from celery import Celery

app = Celery(
    "tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)


app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "fetch-and-save-data-every-5-minutes": {
            "task": "tasks.fetch_and_save_data",
            "schedule": 3600.0,
        },
    },
)

app.autodiscover_tasks(["tasks"])