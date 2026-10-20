from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from db.config import DatabaseConfig
from db.factories import create_sync_database
from exceptions.expired_vehicles import ExpiredVehicleSourceError
from models.vehicle import (
    CarModel,
    CarSaleHistoryModel,
    CarStatus,
    ConditionAssessmentModel,
    PhotoModel,
    RecommendationStatus,
    RelevanceStatus,
)
from schemas.vehicle import CarCreateSchema
from services.expired_vehicle_refresh import refresh_expired_vehicles

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


def car(vin, **kwargs):
    values = dict(
        vin=vin,
        vehicle="old",
        date=NOW,
        relevance=RelevanceStatus.ACTIVE,
        is_manually_upserted=True,
        fuel_type="Gasoline",
        transmision="Automatic",
        suggested_bid=1000,
        recommendation_status=RecommendationStatus.RECOMMENDED,
    )
    values.update(kwargs)
    return CarModel(**values)


async def test_selection_404_errors_and_per_vehicle_commit(database):
    with database.sessions() as db:
        db.add_all(
            [
                car("missing"),
                car("network"),
                car("updated"),
                car("won", car_status=CarStatus.WON),
                car("future", date=NOW + timedelta(seconds=1)),
                car("undated", date=None),
                car("archival", relevance=RelevanceStatus.ARCHIVAL),
            ]
        )
        db.commit()

    def fetch(vin):
        if vin == "missing":
            return None
        if vin == "network":
            raise ExpiredVehicleSourceError("HTTP request failed", RuntimeError("offline"))
        assert vin == "updated"
        # Earlier successful archive must already be visible despite another VIN's error.
        with database.sessions() as observer:
            assert (
                observer.scalar(select(CarModel).where(CarModel.vin == "missing")).relevance
                == RelevanceStatus.ARCHIVAL
            )
        return CarCreateSchema(vin=vin, date=NOW + timedelta(days=1))

    source = Mock(fetch=Mock(side_effect=fetch))
    assert refresh_expired_vehicles(database.sessions, source, NOW) == {
        "checked": 3,
        "archived": 1,
        "reactivated_or_kept_active": 1,
        "deleted_irrelevant": 0,
        "updated": 1,
        "errors": 1,
    }
    assert {call.args[0] for call in source.fetch.call_args_list} == {"missing", "network", "updated"}
    with database.sessions() as db:
        missing = db.scalar(select(CarModel).where(CarModel.vin == "missing"))
        assert missing.is_manually_upserted is False


@pytest.mark.parametrize(
    "auction,date,archived",
    [
        ("Copart", None, True),
        ("Copart", NOW, True),
        ("Copart", NOW + timedelta(seconds=1), False),
        ("BuyNow", None, False),
        ("BUYNOW", NOW - timedelta(seconds=1), False),
    ],
)
async def test_snapshot_final_relevance_and_manual_flag(database, auction, date, archived):
    with database.sessions() as db:
        db.add(car("snapshot"))
        db.commit()
    source = Mock(fetch=Mock(return_value=CarCreateSchema(vin="snapshot", auction_name=auction, date=date)))
    result = refresh_expired_vehicles(database.sessions, source, NOW)
    assert result["updated"] == 1 and result["archived"] == int(archived)
    with database.sessions() as db:
        saved = db.scalar(select(CarModel))
        assert saved.date == date
        assert saved.vehicle == "old"  # None scalars are skipped except date.
        assert saved.is_manually_upserted == (not archived)
        assert saved.relevance == (RelevanceStatus.ARCHIVAL if archived else RelevanceStatus.ACTIVE)


async def test_snapshot_children_and_manual_override_policy(database):
    with database.sessions() as db:
        existing = car("children", recommendation_manually_set=True, recommendation_status_reasons="manual;")
        db.add(existing)
        db.flush()
        db.add(PhotoModel(car_id=existing.id, url="old", is_hd=False))
        db.commit()
    snapshot = CarCreateSchema(
        vin="children",
        fuel_type="diesel",
        current_bid=1500,
        actual_bid=100000,
        date=NOW + timedelta(days=1),
        photos=[{"url": "old"}, {"url": "new"}],
        photos_hd=[{"url": "new"}],
        condition_assessments=[{"type_of_damage": "Primary", "issue_description": "Burn"}],
        sales_history=[{"source": ""} for _ in range(4)],
    )
    result = refresh_expired_vehicles(database.sessions, Mock(fetch=Mock(return_value=snapshot)), NOW)
    assert result["updated"] == 1 and result["errors"] == 0
    with database.sessions() as db:
        saved = db.scalar(select(CarModel))
        assert saved.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
        assert (
            saved.recommendation_status_reasons
            == "manual;diesel;Burn;suggested bid < current bid;sales at auction in the last 3 years: 4;"
        )
        assert saved.recommendation_manually_set is False
        photos = db.scalars(select(PhotoModel)).all()
        assert sorted((p.url, p.is_hd) for p in photos) == [("new", False), ("new", True), ("old", False)]
        assert list(db.scalars(select(ConditionAssessmentModel.issue_description))) == ["Burn"]
        assert list(db.scalars(select(CarSaleHistoryModel.source))) == ["Unknown"] * 4
    # Existing loaded assessments trigger the historical bulk-delete/cascade conflict.
    clean = CarCreateSchema(vin="children", date=NOW + timedelta(days=1), sales_history=[{"source": "Other"}])
    # Select it again by setting its date to expired.
    with database.sessions() as db:
        db.scalar(select(CarModel)).date = NOW
        db.commit()
    result = refresh_expired_vehicles(database.sessions, Mock(fetch=Mock(return_value=clean)), NOW)
    assert result["updated"] == 0 and result["errors"] == 1
    with database.sessions() as db:
        assert list(db.scalars(select(CarSaleHistoryModel.source))) == ["Unknown"] * 4
        assert list(db.scalars(select(ConditionAssessmentModel.issue_description))) == ["Burn"]
        assert db.scalar(select(CarModel)).recommendation_status == RecommendationStatus.NOT_RECOMMENDED


@pytest.mark.parametrize("not_found", [True, False])
async def test_missing_during_update_or_archive_is_skipped(database, not_found):
    with database.sessions() as db:
        db.add(car("gone"))
        db.commit()

    def fetch(vin):
        with database.sessions() as other:
            other.delete(other.scalar(select(CarModel)))
            other.commit()
        return None if not_found else CarCreateSchema(vin=vin)

    result = refresh_expired_vehicles(database.sessions, Mock(fetch=fetch), NOW)
    assert result == {
        "checked": 1,
        "archived": 0,
        "reactivated_or_kept_active": 0,
        "deleted_irrelevant": 0,
        "updated": 0,
        "errors": 0,
    }


@pytest.mark.parametrize("not_found", [True, False])
async def test_commit_failure_rolls_back_snapshot_but_retains_legacy_counters(database, monkeypatch, not_found):
    with database.sessions() as db:
        db.add(car("rollback"))
        db.commit()
    opened = 0

    def sessions():
        nonlocal opened
        opened += 1
        session = database.sessions()
        if opened == 2:
            monkeypatch.setattr(session, "commit", Mock(side_effect=RuntimeError("commit failed")))
        return session

    snapshot = (
        None if not_found else CarCreateSchema(vin="rollback", vehicle="changed", date=None, photos=[{"url": "new"}])
    )
    result = refresh_expired_vehicles(sessions, Mock(fetch=Mock(return_value=snapshot)), NOW)
    assert result["archived"] == (0 if not_found else 1)
    assert result["errors"] == 1 and result["updated"] == 0
    with database.sessions() as db:
        saved = db.scalar(select(CarModel))
        assert saved.relevance == RelevanceStatus.ACTIVE and saved.vehicle == "old"
        assert saved.date == NOW and saved.is_manually_upserted is True
        assert list(db.scalars(select(PhotoModel))) == []


async def test_existing_sales_history_is_not_replaced_or_used_to_override_manual_state(database):
    with database.sessions() as db:
        existing = car("history", recommendation_manually_set=True, recommendation_status_reasons="manual;")
        db.add(existing)
        db.flush()
        db.add(CarSaleHistoryModel(car_id=existing.id, source="Original"))
        db.commit()
    snapshot = CarCreateSchema(
        vin="history", date=NOW + timedelta(days=1), sales_history=[{"source": "Replacement"} for _ in range(5)]
    )
    assert refresh_expired_vehicles(database.sessions, Mock(fetch=Mock(return_value=snapshot)), NOW)["updated"] == 1
    with database.sessions() as db:
        assert list(db.scalars(select(CarSaleHistoryModel.source))) == ["Original"]
        saved = db.scalar(select(CarModel))
        assert saved.recommendation_manually_set is True and saved.recommendation_status_reasons == "manual;"
        assert saved.recommendation_status == RecommendationStatus.RECOMMENDED


async def test_integrity_error_rolls_back_one_vehicle_and_continues(database):
    with database.sessions() as db:
        db.add_all([car("first"), car("second")])
        db.commit()
    source = Mock(
        fetch=Mock(
            side_effect=[
                CarCreateSchema(vin="second", date=None),
                CarCreateSchema(vin="second", date=NOW + timedelta(days=1)),
            ]
        )
    )
    result = refresh_expired_vehicles(database.sessions, source, NOW)
    assert result == {
        "checked": 2,
        "archived": 1,
        "reactivated_or_kept_active": 1,
        "deleted_irrelevant": 0,
        "updated": 1,
        "errors": 1,
    }
    with database.sessions() as db:
        first = db.scalar(select(CarModel).where(CarModel.vin == "first"))
        assert first.relevance == RelevanceStatus.ACTIVE and first.date == NOW
