from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest
from celery.exceptions import MaxRetriesExceededError, Retry
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from crud.vehicle_enrichment import apply_vehicle_enrichment, lock_vehicle_for_enrichment
from db.config import DatabaseConfig
from db.factories import create_sync_database
from domain.enrichment_decisions import decide_vehicle_enrichment
from domain.enrichment_planning import EnrichmentCompletion, RoiProjection, prepare_enrichment
from models.vehicle import AutoCheckModel, CarModel, CarSaleHistoryModel, RecommendationStatus, RelevanceStatus
from services.vehicle_enrichment import complete_vehicle_enrichment, enrichment_live_state

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


def seed(database, *, with_history=False):
    with database.sessions() as db:
        car = CarModel(
            vin="complete",
            vehicle="car",
            mileage=100,
            owners=5,
            accident_count=2,
            attempts=2,
            recommendation_status=RecommendationStatus.RECOMMENDED,
            recommendation_manually_set=True,
            recommendation_status_reasons="upstream parser error;invalid parser json response;scraping error: old;mileage mismatch;",
            relevance=RelevanceStatus.ARCHIVAL,
            has_correct_vin=False,
        )
        db.add(car)
        db.flush()
        if with_history:
            db.add(CarSaleHistoryModel(car_id=car.id, source="Existing"))
        db.commit()


def completion(**changes):
    plan = EnrichmentCompletion(
        preparation=prepare_enrichment({"owners": 1, "mileage": 100, "accident_count": 0}, None, None),
        projection=RoiProjection(),
        auction_fee=0,
        expected_mileage=None,
        business_attempts=0,
        sales_history_rows=[{"source": "Fetched"} for _ in range(4)],
        sales_history_reason="sales at auction in the last 3 years: 4;",
        screenshot_url="http://local/report.html",
    )
    return replace(plan, **changes)


async def test_success_commits_history_report_flags_and_preserves_other_reasons(database):
    seed(database)
    with database.sessions() as db:
        assert complete_vehicle_enrichment(db, "complete", completion()) == {"status": "success", "vin": "complete"}
    with database.sessions() as observer:
        car = observer.scalar(select(CarModel))
        assert car.owners == 1 and car.accident_count == 0 and car.mileage == 100
        assert car.has_correct_vin and car.has_correct_owners and car.has_correct_mileage and car.has_correct_accidents
        assert car.attempts == 3 and car.is_checked and car.relevance == RelevanceStatus.ACTIVE
        assert car.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
        assert car.recommendation_manually_set is False
        assert car.recommendation_status_reasons == "scraping error: old;sales at auction in the last 3 years: 4;"
        assert list(observer.scalars(select(CarSaleHistoryModel.source))) == ["Fetched"] * 4
        assert observer.scalar(select(AutoCheckModel.screenshot_url)) == "http://local/report.html"


@pytest.mark.parametrize("business_attempts,with_history", [(1, False), (0, True)])
async def test_history_is_not_inserted_for_later_attempt_or_existing_history(
    database, business_attempts, with_history
):
    seed(database, with_history=with_history)
    with database.sessions() as db:
        complete_vehicle_enrichment(db, "complete", completion(business_attempts=business_attempts))
    with database.sessions() as db:
        assert list(db.scalars(select(CarSaleHistoryModel.source))) == (["Existing"] if with_history else [])
        car = db.scalar(select(CarModel))
        assert (
            car.recommendation_status == RecommendationStatus.RECOMMENDED and car.recommendation_manually_set is True
        )


async def test_unknown_facts_keep_existing_values_and_clear_validation_flags(database):
    seed(database)
    plan = completion(preparation=prepare_enrichment({}, None, None), sales_history_rows=[], screenshot_url=None)
    with database.sessions() as db:
        complete_vehicle_enrichment(db, "complete", plan)
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.owners == 5 and car.accident_count == 2 and car.mileage == 100
        assert not car.has_correct_owners and not car.has_correct_mileage and not car.has_correct_accidents
        assert car.has_correct_vin and car.is_checked and car.attempts == 3


async def test_mileage_mismatch_uses_task_argument_and_keeps_detailed_old_reasons(database):
    seed(database)
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        car.recommendation_status_reasons = "mileage mismatch: expected 5, parsed 6;"
        db.commit()
    with database.sessions() as db:
        complete_vehicle_enrichment(db, "complete", completion(expected_mileage=101, sales_history_rows=[]))
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.has_correct_mileage is False and car.mileage == 100
        assert (
            car.recommendation_status_reasons
            == "mileage mismatch: expected 5, parsed 6;mileage mismatch: expected 101, parsed 100;"
        )


async def test_nowait_conflict_rolls_back_local_timeouts_and_releases_after_completion(database, postgres_sessions):
    seed(database)
    async with postgres_sessions() as holder:
        await holder.execute(select(CarModel).with_for_update())
        with database.sessions() as db:
            with pytest.raises(OperationalError):
                complete_vehicle_enrichment(db, "complete", completion())
            assert db.scalar(text("SHOW lock_timeout")) == "0"
            assert db.scalar(text("SHOW statement_timeout")) == "0"
        await holder.rollback()
    with database.sessions() as db:
        assert complete_vehicle_enrichment(db, "complete", completion())["status"] == "success"
    async with postgres_sessions() as observer:
        # No lock is retained by the completed service transaction.
        assert (await observer.execute(select(CarModel).with_for_update(nowait=True))).scalar_one().attempts == 3


async def test_missing_row_raises_after_rollback(database):
    with database.sessions() as db:
        with pytest.raises(ValueError, match="Car with VIN absent not found"):
            complete_vehicle_enrichment(db, "absent", completion())
        assert db.scalar(text("SHOW lock_timeout")) == "0"
        assert db.scalar(text("SHOW statement_timeout")) == "0"


@pytest.mark.parametrize("operational", [False, True])
async def test_commit_failure_rolls_back_car_history_report_and_propagates(database, monkeypatch, operational):
    seed(database)
    error = (
        OperationalError("COMMIT", {}, RuntimeError("connection lost"))
        if operational
        else RuntimeError("commit failed")
    )
    with database.sessions() as db:
        monkeypatch.setattr(db, "commit", Mock(side_effect=error))
        with pytest.raises(type(error)):
            complete_vehicle_enrichment(db, "complete", completion())
    with database.sessions() as observer:
        car = observer.scalar(select(CarModel))
        assert car.attempts == 2 and car.owners == 5 and car.relevance == RelevanceStatus.ARCHIVAL
        assert car.has_correct_vin is False and car.recommendation_manually_set is True
        assert list(observer.scalars(select(CarSaleHistoryModel))) == []
        assert list(observer.scalars(select(AutoCheckModel))) == []


async def test_crud_apply_does_not_commit(database):
    seed(database)
    with database.sessions() as db:
        car = lock_vehicle_for_enrichment(db, "complete")
        plan = completion()
        apply_vehicle_enrichment(db, car, plan, decide_vehicle_enrichment(enrichment_live_state(car, plan), plan))
        db.flush()
        db.rollback()
    with database.sessions() as db:
        assert db.scalar(select(CarModel)).attempts == 2
        assert list(db.scalars(select(CarSaleHistoryModel))) == []


@pytest.mark.parametrize("exhausted", [False, True])
async def test_task_retries_after_real_lock_conflict_with_same_countdown(
    database, postgres_sessions, monkeypatch, exhausted
):
    from tasks import task

    seed(database)
    monkeypatch.setattr(task, "SessionLocal", database.sessions)
    monkeypatch.setattr(
        task,
        "http_get_with_retries",
        Mock(return_value=httpx.Response(200, request=httpx.Request("GET", "http://local/parser"), json={})),
    )
    retry = Mock(side_effect=MaxRetriesExceededError() if exhausted else Retry())
    monkeypatch.setattr(task.parse_and_update_car, "retry", retry)
    task.parse_and_update_car.push_request(retries=2)
    try:
        async with postgres_sessions() as holder:
            await holder.execute(select(CarModel).with_for_update())
            if exhausted:
                assert task.parse_and_update_car(vin="complete") == {
                    "status": "locked_skip_after_retries",
                    "vin": "complete",
                }
            else:
                with pytest.raises(Retry):
                    task.parse_and_update_car(vin="complete")
            assert retry.call_args.kwargs["countdown"] == 240
            assert isinstance(retry.call_args.kwargs["exc"], OperationalError)
            await holder.rollback()
    finally:
        task.parse_and_update_car.pop_request()
    with database.sessions() as db:
        assert db.scalar(select(CarModel)).attempts == 2


async def test_task_does_not_retry_non_lock_operational_error(database, monkeypatch):
    from tasks import task

    seed(database)
    opened = 0

    def sessions():
        nonlocal opened
        opened += 1
        session = database.sessions()
        if opened == 3:
            monkeypatch.setattr(
                session, "commit", Mock(side_effect=OperationalError("COMMIT", {}, RuntimeError("connection lost")))
            )
        return session

    monkeypatch.setattr(task, "SessionLocal", sessions)
    monkeypatch.setattr(
        task,
        "http_get_with_retries",
        Mock(return_value=httpx.Response(200, request=httpx.Request("GET", "http://local/parser"), json={})),
    )
    retry = Mock()
    monkeypatch.setattr(task.parse_and_update_car, "retry", retry)
    with pytest.raises(OperationalError, match="connection lost"):
        task.parse_and_update_car(vin="complete")
    retry.assert_not_called()
    with database.sessions() as db:
        assert db.scalar(select(CarModel)).attempts == 2
