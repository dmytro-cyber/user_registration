from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from db.config import DatabaseConfig
from db.factories import create_sync_database
from models.admin import FilterModel
from models.filter_kickoff_queue import FilterKickoffQueueModel, FilterKickoffQueueStatus
from models.vehicle import CarModel
from services.filter_execution import execute_filter_kickoff

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


def seed(database, model=None):
    with database.sessions() as db:
        filt = FilterModel(make="Toyota", model=model, year_from=2010, year_to=2020, odometer_min=10, odometer_max=100)
        db.add(filt)
        db.flush()
        job = FilterKickoffQueueModel(
            filter_id=filt.id,
            scheduled_at=datetime.now(timezone.utc),
            status=FilterKickoffQueueStatus.DISPATCHED,
            attempts=2,
            last_error="old",
        )
        db.add(job)
        for index, changes in enumerate(
            [
                {},
                {"model": "Other"},
                {"make": "Honda"},
                {"year": 2009},
                {"year": 2021},
                {"mileage": 9},
                {"mileage": 101},
                {"avg_market_price": 1},
                {"is_checked": True},
                {"attempts": 3},
            ]
        ):
            values = dict(
                vin=f"VIN-{index}",
                vehicle="Toyota car",
                engine_title="engine",
                make="Toyota",
                model="Camry",
                year=2015,
                mileage=50,
                transmision="Automatic",
                is_checked=False,
                attempts=0,
            )
            values.update(changes)
            db.add(CarModel(**values))
        db.commit()
        return filt.id, job.id


@pytest.mark.parametrize("batch_size", [1, 2, 100, 0, -1])
async def test_execution_matches_bounds_and_closes_read_session_before_publication(database, batch_size):
    filter_id, job_id = seed(database)
    calls = []

    def publish(payload):
        assert database.engine.pool.checkedout() == 0
        calls.append(payload)

    result = execute_filter_kickoff(database.sessions, Mock(publish=publish), filter_id, str(job_id), batch_size)
    assert result == {"status": "ok", "filter_id": filter_id, "enqueued": 2}
    assert {p["vin"] for p in calls} == {"VIN-0", "VIN-1"}
    assert calls[0] == dict(
        vin=calls[0]["vin"],
        car_name="Toyota car",
        car_engine="engine",
        mileage=50,
        car_make="Toyota",
        car_model="Camry",
        car_year=2015,
        car_transmison="Automatic",
    )
    with database.sessions() as db:
        job = db.get(FilterKickoffQueueModel, job_id)
        assert job.status == FilterKickoffQueueStatus.DONE
        assert job.finished_at is not None and job.last_error is None and job.attempts == 2


async def test_exact_model_and_partial_publication_failure(database):
    filter_id, job_id = seed(database, model="Camry")
    publisher = Mock()
    execute_filter_kickoff(database.sessions, publisher, filter_id, str(job_id))
    assert publisher.publish.call_count == 1
    # A wildcard filter selects two vehicles. The first message survives failure of the second.
    with database.sessions() as db:
        db.get(FilterModel, filter_id).model = None
        db.commit()
    publisher = Mock()
    publisher.publish.side_effect = [None, RuntimeError("broker failed")]
    with pytest.raises(RuntimeError, match="broker failed"):
        execute_filter_kickoff(database.sessions, publisher, filter_id, str(job_id))
    assert publisher.publish.call_count == 2
    with database.sessions() as db:
        job = db.get(FilterKickoffQueueModel, job_id)
        assert job.status == FilterKickoffQueueStatus.FAILED and job.last_error == "broker failed"
        assert job.attempts == 2


async def test_missing_filter_marks_provided_job_failed(database):
    _, job_id = seed(database)
    publisher = Mock()
    assert execute_filter_kickoff(database.sessions, publisher, -1, str(job_id)) == {
        "status": "error",
        "reason": "filter_not_found",
        "filter_id": -1,
    }
    publisher.publish.assert_not_called()
    with database.sessions() as db:
        assert db.get(FilterKickoffQueueModel, job_id).last_error == "filter_not_found"


async def test_missing_queue_item_and_empty_selection_are_successful(database):
    filter_id, _ = seed(database, model="Absent")
    publisher = Mock()
    assert execute_filter_kickoff(database.sessions, publisher, filter_id, "-1") == {
        "status": "ok",
        "filter_id": filter_id,
        "enqueued": 0,
    }
    publisher.publish.assert_not_called()


async def test_invalid_token_does_not_open_session(database):
    sessions = Mock()
    with pytest.raises(ValueError):
        execute_filter_kickoff(sessions, Mock(), 1, "invalid")
    sessions.assert_not_called()


async def test_task_preserves_delay_routing_and_legacy_arguments(database, monkeypatch):
    from tasks import task

    filter_id, job_id = seed(database)
    monkeypatch.setattr(task, "SessionLocal", database.sessions)
    delay = Mock(return_value=object())
    monkeypatch.setattr(task.parse_and_update_car, "delay", delay)
    assert task.kickoff_parse_for_filter.name == "tasks.task.kickoff_parse_for_filter"
    assert task.kickoff_parse_for_filter(filter_id, str(job_id), batch_size=1, stream_chunk=1)["enqueued"] == 2
    assert delay.call_count == 2
    assert set(delay.call_args.kwargs) == {
        "vin",
        "car_name",
        "car_engine",
        "mileage",
        "car_make",
        "car_model",
        "car_year",
        "car_transmison",
    }


async def test_completion_commit_failure_records_failed_in_fresh_session(database, monkeypatch):
    filter_id, job_id = seed(database)
    opened = 0

    def sessions():
        nonlocal opened
        opened += 1
        session = database.sessions()
        if opened == 2:
            monkeypatch.setattr(session, "commit", Mock(side_effect=RuntimeError("completion commit failed")))
        return session

    publisher = Mock()
    with pytest.raises(RuntimeError, match="completion commit failed"):
        execute_filter_kickoff(sessions, publisher, filter_id, str(job_id))
    assert publisher.publish.call_count == 2 and opened == 3
    with database.sessions() as db:
        job = db.get(FilterKickoffQueueModel, job_id)
        assert job.status == FilterKickoffQueueStatus.FAILED
        assert job.last_error == "completion commit failed" and job.attempts == 2
