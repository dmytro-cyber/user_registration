import asyncio
import threading

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker

from core.celery_settings import scope_broker_settings
from core.integration_celery import app
from integrations import scraping_rpc
from tasks import ingestion, integrations


def test_task_names_and_schedules_remain_compatible():
    assert ingestion.fetch_api_data.name == "tasks.tasks.fetch_api_data"
    assert ingestion.delete_vehicle.name == "tasks.tasks.delete_vehicle"
    assert app.conf.timezone == "UTC"
    assert app.conf.beat_schedule["fetch-api-data-every-hour"]["schedule"]._orig_minute == "*/60"
    assert app.conf.beat_schedule["delete-vehicles-evry-hour-at-0:15"]["schedule"]._orig_minute == 5
    assert app.conf.task_routes["tasks.integrations.execute"]["queue"] == "scraping"


def test_generic_celery_env_cannot_cross_wire_scoped_brokers(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://wrong-host:6379/9")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://wrong-host:6379/9")
    isolated = Celery("scoped-test", broker="memory://", backend="cache+memory://", set_as_current=False)
    scope_broker_settings(isolated)
    try:
        assert isolated.conf.broker_url == "memory://"
        assert isolated.conf.broker_write_url == "memory://"
        assert isolated.conf.result_backend == "cache+memory://"
        assert isolated.connection_for_write().transport_cls == "memory"
    finally:
        isolated.close()


def test_worker_runner_disposes_async_database_on_its_own_loop_before_closing(monkeypatch):
    from db import session

    loops = []
    disposed = []

    async def operation():
        loops.append(asyncio.get_running_loop())

    async def dispose():
        disposed.append(asyncio.get_running_loop())

    monkeypatch.setattr(integrations, "_runner", None)
    monkeypatch.setattr(session, "dispose_database", dispose)
    try:
        integrations.run_async(operation)
        integrations.close_runner()
        assert disposed == loops
        assert loops[0].is_closed()
        integrations.run_async(operation)
        assert loops[1] is not loops[0]
    finally:
        integrations.close_runner()
    assert disposed == loops
    assert all(loop.is_closed() for loop in loops)


def test_scraping_rpc_executes_in_worker_with_a_reused_event_loop(monkeypatch):
    pytest.importorskip("bs4")
    pytest.importorskip("webdriver_manager")
    from integrations.fees import copart

    observed = []

    def fees():
        observed.append((threading.get_ident(), id(asyncio.get_running_loop())))
        return {"fees": {}}

    monkeypatch.setattr(copart, "scrape_copart_fees", fees)
    isolated = Celery("worker-test", broker="memory://", backend="cache+memory://", set_as_current=False)
    scope_broker_settings(isolated)
    isolated.task(name="tasks.integrations.execute")(integrations.execute.run)
    monkeypatch.setattr(scraping_rpc, "app", isolated)
    try:
        with start_worker(isolated, pool="solo", concurrency=1, queues=["scraping"], perform_ping_check=False):
            assert scraping_rpc.execute("copart_fees", {}, timeout=10) == {
                "status": 200,
                "body": {"fees": {"copart": {"fees": {}}}},
            }
            assert scraping_rpc.execute("copart_fees", {}, timeout=10)["status"] == 200
        assert observed[0][0] != threading.get_ident()
        assert observed[0] == observed[1]
    finally:
        isolated.close()
