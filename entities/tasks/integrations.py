"""Serialized browser/OCR work; one event loop and auth state per solo worker."""

import asyncio
import atexit

from core.integration_celery import app

_runner = None


def close_runner():
    global _runner
    runner, _runner = _runner, None
    if runner is not None:
        from db.session import dispose_database

        try:
            runner.run(dispose_database())
        finally:
            runner.close()


atexit.register(close_runner)


def run_async(function, *args, **kwargs):
    global _runner
    if _runner is None:
        _runner = asyncio.Runner()
    return _runner.run(function(*args, **kwargs))


@app.task(name="tasks.integrations.execute")
def execute(operation, payload):
    from integrations.scraping_operations import execute_operation

    return run_async(execute_operation, operation, payload)
