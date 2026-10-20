from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from db.config import DatabaseConfig
from db.factories import create_sync_database
from exceptions.vehicle_enrichment import EnrichmentLockConflict
from models.vehicle import AutoCheckModel, CarModel, CarSaleHistoryModel
from schemas.vehicle import CarCreateSchema
from services.vehicle_enrichment_workflow import enrich_vehicle

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


def seed(database, attempts=0):
    with database.sessions() as db:
        db.add(CarModel(vin="workflow", vehicle="car", attempts=attempts, mileage=100))
        db.commit()


def request():
    return {
        "vin": "workflow",
        "car_name": None,
        "car_engine": None,
        "mileage": 100,
        "car_make": None,
        "car_model": None,
        "car_year": None,
        "car_transmison": None,
    }


async def test_workflow_runs_through_ports_and_closes_sessions_between_io(database):
    seed(database)
    events = []

    def history(vin):
        assert database.engine.pool.checkedout() == 0
        events.append("history")
        return CarCreateSchema(vin=vin, sales_history=[{"source": ""} for _ in range(4)])

    def parse(payload):
        assert database.engine.pool.checkedout() == 0 and payload == request()
        events.append("parser")
        return {"owners": 2, "mileage": 100, "accident_count": 0, "html_data": "report"}

    def save(vin, html):
        assert database.engine.pool.checkedout() == 0 and (vin, html) == ("workflow", "report")
        events.append("report")
        with database.sessions() as observer:
            assert observer.scalar(select(CarModel.attempts)) == 0
        return "http://local/report"

    source = Mock(fetch_history=history, parse=parse)
    assert enrich_vehicle(database.sessions, request(), source, lambda: Mock(save=save)) == {
        "status": "success",
        "vin": "workflow",
    }
    assert events == ["history", "parser", "report"]
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.attempts == 1 and car.owners == 2 and car.has_correct_mileage
        assert list(db.scalars(select(CarSaleHistoryModel.source))) == ["Unknown"] * 4
        assert db.scalar(select(AutoCheckModel.screenshot_url)) == "http://local/report"


async def test_later_attempt_skips_history_and_empty_html_does_not_build_storage(database):
    seed(database, attempts=1)
    source = Mock()
    source.parse.return_value = {}
    factory = Mock()
    assert enrich_vehicle(database.sessions, request(), source, factory)["status"] == "success"
    source.fetch_history.assert_not_called()
    factory.assert_not_called()


async def test_missing_vehicle_fails_before_external_calls(database):
    source, factory = Mock(), Mock()
    with pytest.raises(ValueError, match="Car with VIN workflow not found"):
        enrich_vehicle(database.sessions, request(), source, factory)
    assert source.mock_calls == []
    factory.assert_not_called()
    assert database.engine.pool.checkedout() == 0


@pytest.mark.parametrize("stage", [1, 2])
async def test_task_does_not_retry_read_or_pricing_lock_error(database, monkeypatch, stage):
    from tasks import task

    seed(database, attempts=1)
    opened = 0
    error = OperationalError("SELECT", {}, RuntimeError("lock timeout"))

    def sessions():
        nonlocal opened
        opened += 1
        db = database.sessions()
        if opened == stage:
            monkeypatch.setattr(db, "execute", Mock(side_effect=error))
        return db

    monkeypatch.setattr(task, "SessionLocal", sessions)
    monkeypatch.setattr(
        task,
        "http_get_with_retries",
        Mock(return_value=httpx.Response(200, request=httpx.Request("GET", "http://local/parser"), json={})),
    )
    retry = Mock()
    monkeypatch.setattr(task.parse_and_update_car, "retry", retry)
    with pytest.raises(OperationalError) as caught:
        task.parse_and_update_car(vin="workflow")
    assert caught.value is error
    retry.assert_not_called()
    with database.sessions() as db:
        assert db.scalar(select(CarModel.attempts)) == 1


async def test_only_final_nowait_conflict_is_translated_for_celery(database, postgres_sessions):
    seed(database, attempts=1)
    source = Mock()
    source.parse.return_value = {}
    async with postgres_sessions() as holder:
        await holder.execute(select(CarModel).with_for_update())
        with pytest.raises(EnrichmentLockConflict) as caught:
            enrich_vehicle(database.sessions, request(), source, Mock())
        assert isinstance(caught.value.error, OperationalError) and caught.value.__cause__ is caught.value.error
        assert database.engine.pool.checkedout() == 0
        await holder.rollback()
    with database.sessions() as db:
        assert db.scalar(select(CarModel.attempts)) == 1


async def test_planning_failure_does_not_upload_or_record_an_attempt(database):
    seed(database, attempts=1)
    source = Mock()
    source.parse.return_value = {"owners": float("nan"), "html_data": "report"}
    factory = Mock()
    with pytest.raises(ValueError):
        enrich_vehicle(database.sessions, request(), source, factory)
    factory.assert_not_called()
    with database.sessions() as db:
        assert db.scalar(select(CarModel.attempts)) == 1
