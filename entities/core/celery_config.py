# from gevent import monkey
# monkey.patch_all(ssl=True, socket=True, dns=True, time=True, select=True, subprocess=True, os=True)

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

@worker_process_init.connect
def _gevent_patch_in_child(**_):
    from gevent import monkey
    monkey.patch_all()

app = Celery("tasks", broker="redis://redis_1:6380/0", backend="redis://redis_1:6380/0")

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

app.conf.beat_schedule = {
    "update-car-bids-every-15-minutes": {
        "task": "tasks.task.update_car_bids",
        "schedule": crontab(minute="15-59/15"),
    },
    "update-fees-every-1-month": {
        "task": "tasks.task.update_fees",
        "schedule": crontab(day_of_month="1", hour=0, minute=0),
    },
    "expired-auction-daily-3am-CT": {  # ⬅️ НОВЕ
        "task": "tasks.task.parse_and_update_cars_with_expired_auction_date",
        "schedule": crontab(hour=3, minute=0, timezone="America/Chicago"),  # 03:00 Central Time
        "options": {"queue": "car_parsing_queue"},
    },
}

app.autodiscover_tasks(["tasks.task"])
