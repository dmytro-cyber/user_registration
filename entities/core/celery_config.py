# from gevent import monkey
# monkey.patch_all(ssl=True, socket=True, dns=True, time=True, select=True, subprocess=True, os=True)

from celery import Celery
from celery.schedules import crontab

app = Celery("tasks", broker="redis://redis_1:6380/0", backend="redis://redis_1:6380/0")

app.conf.task_queues = {
    "default": {"exchange": "default", "routing_key": "default"},
    "car_parsing_queue": {"exchange": "car_parsing_queue", "routing_key": "car_parsing_queue"},
}

app.conf.task_routes = {
    "tasks.task.parse_and_update_car": {"queue": "car_parsing_queue"},
    "tasks.task.update_car_bids": {"queue": "car_parsing_queue"},
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
}

app.autodiscover_tasks(["tasks.task"])
