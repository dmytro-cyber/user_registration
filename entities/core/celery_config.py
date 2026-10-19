import os

from celery.signals import worker_process_init, worker_process_shutdown, worker_shutdown


@worker_process_init.connect
def _initialize_worker_process(**_):
    if os.getenv("CELERY_GEVENT") == "1":
        from gevent import monkey
        monkey.patch_all()
    from db.sync_session import dispose_database

    dispose_database(close=False)


@worker_process_shutdown.connect
@worker_shutdown.connect
def _dispose_worker_database(**_):
    from db.sync_session import dispose_database

    dispose_database()

from celery import Celery
from celery.schedules import crontab

from core.celery_settings import scope_broker_settings

app = Celery("tasks", broker=os.getenv("ENTITIES_BROKER_URL", "redis://redis:6379/1"),
             backend=os.getenv("ENTITIES_RESULT_BACKEND", "redis://redis:6379/1"))

app.conf.timezone = "America/Chicago"

app.conf.task_queues = {
    "default": {"exchange": "default", "routing_key": "default"},
    "car_parsing_queue": {"exchange": "car_parsing_queue", "routing_key": "car_parsing_queue"},
}

app.conf.task_routes = {
    "tasks.task.parse_and_update_car": {"queue": "car_parsing_queue"},
    "tasks.task.update_car_bids": {"queue": "car_parsing_queue"},
    "tasks.task.parse_and_update_cars_with_expired_auction_date": {"queue": "car_parsing_queue"},
}

app.conf.task_track_started = True
app.conf.task_serializer = "json"
app.conf.accept_content = ["json"]
app.conf.result_serializer = "json"
app.conf.task_always_eager = False

app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True
app.conf.worker_prefetch_multiplier = 1
app.conf.broker_connection_retry_on_startup = True
app.conf.broker_connection_retry = True

app.conf.beat_schedule = {
    "update-car-bids-every-15-minutes": {
        "task": "tasks.task.update_car_bids",
        "schedule": crontab(minute="30", hour="0-2,4-23"),
    },
    # "update-fees-every-1-month": {
    #     "task": "tasks.task.update_fees",
    #     "schedule": crontab(day_of_month="1", hour=0, minute=0),
    # },
    "expired-auction-daily-3-15am-CT": {
        "task": "tasks.task.parse_and_update_cars_with_expired_auction_date",
        "schedule": crontab(hour=3, minute=15),  # 03:15 CT
        "options": {"queue": "car_parsing_queue"},
    },
    "daily-updated-via-extention-logs": {
        "task": "tasks.task.send_daily_car_audit",
        "schedule": crontab(hour=0),
        "options": {"queue": "car_parsing_queue"},
    },
    "dispatch_due_filter_kickoff_jobs_every_minute": {
        "task": "tasks.task.dispatch_due_filter_kickoff_jobs",
        "schedule": crontab(minute="*"),
        "options": {"queue": "car_parsing_queue"},
    },
}

app.autodiscover_tasks(["tasks.task"])
scope_broker_settings(app)
