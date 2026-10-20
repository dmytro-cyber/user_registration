"""Only this adapter knows how application calls reach the browser/OCR worker."""

from celery.result import allow_join_result

from core.integration_celery import app


def execute(operation, payload, *, timeout=60):
    result = app.send_task(
        "tasks.integrations.execute",
        kwargs={"operation": operation, "payload": payload},
        queue="scraping",
    )
    # The scraping queue is consumed exclusively by a separate solo worker.
    with allow_join_result():
        return result.get(timeout=timeout, propagate=True)
