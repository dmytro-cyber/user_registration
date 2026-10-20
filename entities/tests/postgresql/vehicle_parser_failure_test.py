from datetime import datetime, timezone
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from db.config import DatabaseConfig
from db.factories import create_sync_database
from models.vehicle import CarModel, RecommendationStatus, RelevanceStatus
from services.vehicle_enrichment import record_vehicle_parser_failure

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


def seed(database, attempts=2):
    with database.sessions() as db:
        db.add(
            CarModel(
                vin="failure",
                vehicle="car",
                attempts=attempts,
                has_correct_vin=True,
                is_checked=True,
                date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                relevance=RelevanceStatus.ARCHIVAL,
                recommendation_status=RecommendationStatus.RECOMMENDED,
                recommendation_manually_set=True,
                recommendation_status_reasons="manual;",
            )
        )
        db.commit()


@pytest.mark.parametrize(
    "reason", ["upstream parser error;", "invalid parser json response;", "scraping error: invalid VIN;"]
)
async def test_failure_commits_attempts_and_reason_without_changing_other_state(database, reason):
    seed(database)
    for _ in range(2):
        with database.sessions() as db:
            assert record_vehicle_parser_failure(db, "failure", reason) == {"status": "exception", "vin": "failure"}
    with database.sessions() as observer:
        car = observer.scalar(select(CarModel))
        assert car.attempts == 4 and car.has_correct_vin is False
        assert car.recommendation_status_reasons == "manual;" + reason
        assert (
            car.recommendation_status == RecommendationStatus.RECOMMENDED and car.recommendation_manually_set is True
        )
        assert car.is_checked is True and car.relevance == RelevanceStatus.ARCHIVAL
        assert car.date == datetime(2026, 1, 1, tzinfo=timezone.utc)


async def test_nowait_conflict_rolls_back_timeout_and_can_record_after_release(database, postgres_sessions):
    seed(database)
    async with postgres_sessions() as holder:
        await holder.execute(select(CarModel).with_for_update())
        with database.sessions() as db:
            assert record_vehicle_parser_failure(db, "failure", "upstream parser error;") == {
                "status": "locked_skip",
                "vin": "failure",
            }
            # A subsequent query succeeds and the transaction-local timeout has been rolled back.
            assert db.scalar(text("SHOW lock_timeout")) == "0"
            assert db.scalar(select(CarModel.attempts)) == 2
        await holder.rollback()
    with database.sessions() as db:
        assert record_vehicle_parser_failure(db, "failure", "upstream parser error;")["status"] == "exception"


async def test_missing_vehicle_does_not_commit(database, monkeypatch):
    with database.sessions() as db:
        commit = Mock()
        monkeypatch.setattr(db, "commit", commit)
        assert record_vehicle_parser_failure(db, "absent", "reason") == {"status": "not_found", "vin": "absent"}
        commit.assert_not_called()
        assert db.scalar(text("SHOW lock_timeout")) == "2s"


@pytest.mark.parametrize("operational", [True, False])
async def test_commit_failure_rolls_back_and_preserves_existing_error_policy(database, monkeypatch, operational):
    seed(database)
    failure = (
        OperationalError("COMMIT", {}, RuntimeError("connection lost")) if operational else RuntimeError("failed")
    )
    with database.sessions() as db:
        monkeypatch.setattr(db, "commit", Mock(side_effect=failure))
        if operational:
            # Legacy behavior maps every OperationalError to locked_skip, including non-lock errors.
            assert record_vehicle_parser_failure(db, "failure", "new reason;") == {
                "status": "locked_skip",
                "vin": "failure",
            }
        else:
            with pytest.raises(RuntimeError, match="failed"):
                record_vehicle_parser_failure(db, "failure", "new reason;")
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.attempts == 2 and car.has_correct_vin is True and car.recommendation_status_reasons == "manual;"


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("http", "upstream parser error;"),
        ("json", "invalid parser json response;"),
        ("payload", "scraping error: bad VIN;"),
    ],
)
async def test_celery_failure_branches_keep_result_reason_and_skip_external_writes(
    database, monkeypatch, kind, reason
):
    from tasks import task

    seed(database, attempts=1)
    monkeypatch.setattr(task, "SessionLocal", database.sessions)
    request = httpx.Request("GET", "http://parsers:8001/api/v1/parsers/scrape/dc")
    response = (
        httpx.Response(200, request=request, content=b"not json")
        if kind == "json"
        else httpx.Response(200, request=request, json={"error": "  bad VIN  ", "html_data": "<html>ignored</html>"})
    )
    fetch = Mock(side_effect=RuntimeError("offline")) if kind == "http" else Mock(return_value=response)
    monkeypatch.setattr(task, "http_get_with_retries", fetch)
    storage = Mock()
    monkeypatch.setattr(task, "S3StorageClient", storage)
    assert task.parse_and_update_car(vin="failure") == {"status": "exception", "vin": "failure"}
    assert task.parse_and_update_car.name == "tasks.task.parse_and_update_car"
    assert task.parse_and_update_car.max_retries == 3 and task.parse_and_update_car.default_retry_delay == 60
    assert fetch.call_count == 1 and fetch.call_args.kwargs["timeout"] == 300.0
    storage.assert_not_called()
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.attempts == 2 and car.recommendation_status_reasons == "manual;" + reason


async def test_task_returns_not_found_if_vehicle_disappears_during_fetch(database, monkeypatch):
    from tasks import task

    seed(database, attempts=1)
    monkeypatch.setattr(task, "SessionLocal", database.sessions)

    def fetch(*args, **kwargs):
        with database.sessions() as other:
            other.delete(other.scalar(select(CarModel)))
            other.commit()
        raise RuntimeError("offline")

    monkeypatch.setattr(task, "http_get_with_retries", fetch)
    assert task.parse_and_update_car(vin="failure") == {"status": "not_found", "vin": "failure"}
