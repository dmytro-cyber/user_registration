# from gevent import monkey
# monkey.patch_all()

import tasks.task  # імпорт усіх тасок (без повторного patch)
from core.celery_config import app  # ініціалізація Celery
