from gevent import monkey
monkey.patch_all()

from core.celery_config import app  # ініціалізація Celery
import tasks.task                    # імпорт усіх тасок (без повторного patch)
