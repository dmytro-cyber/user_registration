from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from db.config import DatabaseConfig
from db.factories import create_sync_database
from models.vehicle import CarModel, RecommendationStatus, RelevanceStatus
from services.vehicle_bids import refresh_vehicle_bids

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def sync_database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


class Source:
    def __init__(self, payload):
        self.payload = payload
        self.targets = []

    def fetch(self, targets):
        self.targets = targets
        return self.payload


def vehicle(vin, lot, **changes):
    return CarModel(
        vin=vin,
        vehicle="Test vehicle",
        lot=lot,
        auction="Copart",
        relevance=RelevanceStatus.ACTIVE,
        current_bid=1000,
        **{"date": datetime.now(timezone.utc), "suggested_bid": 5000, **changes},
    )


@pytest.mark.parametrize("wrapped", [True, False])
async def test_refresh_selection_skips_invalid_bids_and_commits_math(sync_database, postgres_sessions, wrapped):
    async with postgres_sessions() as db:
        db.add_all(
            [
                vehicle("BID-DATED", 123, actual_bid=1500, maintenance=1000, avg_market_price=10000),
                vehicle("BID-BUYNOW", 124, date=None, auction_name="Buynow"),
                vehicle("BID-NO-DATE", 125, date=None),
            ]
        )
        await db.commit()
    bids = [
        {"lot_id": "123-1", "site": "Copart.COM", "pre_bid": "3000.9"},
        {"lot_id": "124", "site": 1, "pre_bid": 0},
        {"lot_id": "123", "site": 2, "pre_bid": 9000},
        {"lot_id": "invalid", "site": 1, "pre_bid": 9999},
        {"lot_id": "123", "site": 1, "pre_bid": None},
        {"lot_id": "123", "site": 1, "pre_bid": "invalid"},
    ]
    source = Source({"bids": bids} if wrapped else bids)
    with sync_database.sessions() as db:
        assert refresh_vehicle_bids(db, source) == {"status": "success", "updated_cars": 2}
    assert {target.lot for target in source.targets} == {123, 124}
    async with postgres_sessions() as db:
        cars = {car.vin: car for car in (await db.execute(select(CarModel))).scalars()}
        assert cars["BID-DATED"].current_bid == 3000
        assert cars["BID-DATED"].predicted_profit_margin == 6000
        assert cars["BID-DATED"].predicted_roi == 150
        assert cars["BID-DATED"].recommendation_status == RecommendationStatus.NOT_RECOMMENDED
        assert cars["BID-BUYNOW"].current_bid == 0
        assert cars["BID-NO-DATE"].current_bid == 1000


async def test_locked_vehicle_is_skipped_then_updates_after_release(sync_database, postgres_sessions):
    async with postgres_sessions() as db:
        db.add(vehicle("BID-LOCK", 123))
        await db.commit()
    source = Source([{"lot_id": "123", "site": 1, "pre_bid": 6000}])
    async with postgres_sessions() as holder:
        await holder.execute(select(CarModel).with_for_update())
        with sync_database.sessions() as db:
            assert refresh_vehicle_bids(db, source)["updated_cars"] == 0
        await holder.rollback()
    with sync_database.sessions() as db:
        assert refresh_vehicle_bids(db, source)["updated_cars"] == 1


@pytest.mark.parametrize("failure", ["commit", "source", "malformed_item"])
async def test_batch_failures_roll_back_prior_bid_changes(sync_database, postgres_sessions, monkeypatch, failure):
    async with postgres_sessions() as db:
        db.add(vehicle("BID-ROLLBACK", 123))
        await db.commit()
    source = Source([{"lot_id": "123", "site": 1, "pre_bid": 6000}])
    if failure == "source":
        source.fetch = Mock(side_effect=RuntimeError("source failed"))
    elif failure == "malformed_item":
        source.payload.append("invalid item")
    with sync_database.sessions() as db:
        if failure == "commit":
            monkeypatch.setattr(db, "commit", Mock(side_effect=RuntimeError("commit failed")))
        with pytest.raises((RuntimeError, AttributeError)):
            refresh_vehicle_bids(db, source)
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert car.current_bid == 1000
        assert car.recommendation_status_reasons is None


async def test_empty_selection_does_not_fetch(sync_database):
    source = Source([])
    source.fetch = Mock(side_effect=AssertionError("Source must not be called"))
    with sync_database.sessions() as db:
        assert refresh_vehicle_bids(db, source) == {"status": "success", "updated_cars": 0}
    source.fetch.assert_not_called()
