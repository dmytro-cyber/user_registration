"""Prevent broker publication in tests that exercise application boundaries."""

from types import SimpleNamespace

import pytest


@pytest.fixture(scope="function", autouse=True)
def isolate_celery(monkeypatch):
    """
    Stub send_task publication; individual task tests mock their other I/O.
    """
    from core.celery_config import app as celery_app
    from core.integration_celery import app as integration_app

    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda *args, **kwargs: SimpleNamespace(id="test-task-id", state="SUCCESS"),
    )
    monkeypatch.setattr(
        integration_app,
        "send_task",
        lambda *args, **kwargs: SimpleNamespace(id="test-integration-task-id", state="SUCCESS"),
    )
