from celery import Celery

app = Celery('tasks', broker='redis://redis_1:6380/0', backend='redis://redis_1:6380/0')

app.conf.task_queues = {
    'default': {'exchange': 'default', 'routing_key': 'default'},
    'car_parsing_queue': {'exchange': 'car_parsing_queue', 'routing_key': 'car_parsing_queue'}
}

app.conf.task_routes = {
    'tasks.task.parse_and_update_car': {'queue': 'car_parsing_queue'}
}

app.conf.task_track_started = True
app.conf.task_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.result_serializer = 'json'
app.conf.task_always_eager = False

app.autodiscover_tasks(['tasks.task'])