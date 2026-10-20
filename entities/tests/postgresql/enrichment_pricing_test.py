from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from crud.enrichment_pricing import load_default_roi, load_enrichment_fees
from db.config import DatabaseConfig
from db.factories import create_sync_database
from domain.enrichment_planning import apply_enrichment_fees
from models.admin import ROIModel
from models.vehicle import CarModel, FeeModel

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


def seed_pricing(database):
    with database.sessions() as db:
        db.add_all(
            [ROIModel(roi=10, created_at=datetime(2026, 1, 1)), ROIModel(roi=25, created_at=datetime(2026, 1, 2))]
        )
        db.add_all(
            [
                FeeModel(auction="Copart", fee_type="fixed", amount=100, percent=False, price_from=0, price_to=16000),
                FeeModel(
                    auction="Copart", fee_type="percent", amount=5, percent=True, price_from=16000, price_to=50000
                ),
                FeeModel(
                    auction="copart", fee_type="other case", amount=999, percent=False, price_from=0, price_to=50000
                ),
            ]
        )
        db.commit()


async def test_pricing_reads_latest_roi_inclusive_ranges_and_exact_auction_without_commit(database, monkeypatch):
    seed_pricing(database)
    with database.sessions() as db:
        commit = Mock()
        monkeypatch.setattr(db, "commit", commit)
        roi = load_default_roi(db)
        assert roi.roi == 25 and roi.profit_margin == 20
        fees = load_enrichment_fees(db, "Copart", 16000)
        assert {f.fee_type for f in fees} == {"fixed", "percent"}
        assert apply_enrichment_fees(16000, fees) == 900
        assert len(load_enrichment_fees(db, "Copart", 15999)) == 1
        assert load_enrichment_fees(db, None, 16000) == []
        commit.assert_not_called()


async def test_task_applies_real_projection_and_fees(database, monkeypatch):
    from tasks import task

    seed_pricing(database)
    with database.sessions() as db:
        db.add(
            CarModel(
                vin="projection",
                vehicle="car",
                attempts=1,
                auction="Copart",
                mileage=100000,
                transportation=500,
                parts_cost=1000,
            )
        )
        db.commit()
    monkeypatch.setattr(task, "SessionLocal", database.sessions)
    payload = {
        "jd": 20000,
        "d_max": 20000,
        "manheim": 20000,
        "owners": "2 owners",
        "mileage": "100,000",
        "accident_count": "0 accidents",
    }
    monkeypatch.setattr(
        task,
        "http_get_with_retries",
        Mock(return_value=httpx.Response(200, request=httpx.Request("GET", "http://local/parser"), json=payload)),
    )
    assert task.parse_and_update_car(vin="projection") == {"status": "success", "vin": "projection"}
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.avg_market_price == 20000 and car.predicted_total_investments == 16000
        assert car.predicted_profit_margin_percent == 20 and car.predicted_profit_margin == 4000
        assert car.predicted_roi == 25 and car.auction_fee == 900 and car.suggested_bid == 13600
        assert car.owners == 2 and car.accident_count == 0
        assert car.has_correct_mileage is True and car.attempts == 2


@pytest.mark.parametrize("change_price", [True, False])
async def test_task_preserves_market_fields_changed_during_parser_call(database, monkeypatch, change_price):
    from tasks import task

    seed_pricing(database)
    with database.sessions() as db:
        db.add(
            CarModel(
                vin="concurrent",
                vehicle="car",
                attempts=1,
                auction="Copart",
                avg_market_price=20000,
                market_price_sources={"jd": 20000, "d_max": 20000, "manheim": 20000},
                predicted_total_investments=12345,
                suggested_bid=11000,
                auction_fee=100,
            )
        )
        db.commit()
    monkeypatch.setattr(task, "SessionLocal", database.sessions)

    def fetch(*args, **kwargs):
        with database.sessions() as other:
            car = other.scalar(select(CarModel))
            if change_price:
                car.avg_market_price = 30000
            else:
                car.market_price_sources = {"manual": 20000}
            car.predicted_total_investments = 22222
            car.suggested_bid = 21000
            car.auction_fee = 777
            other.commit()
        return httpx.Response(
            200,
            request=httpx.Request("GET", "http://local/parser"),
            json={"jd": 25000, "d_max": 25000, "manheim": 25000},
        )

    monkeypatch.setattr(task, "http_get_with_retries", fetch)
    assert task.parse_and_update_car(vin="concurrent")["status"] == "success"
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.avg_market_price == (30000 if change_price else 20000)
        assert car.market_price_sources == (
            {"jd": 20000, "d_max": 20000, "manheim": 20000} if change_price else {"manual": 20000}
        )
        assert car.predicted_total_investments == 22222 and car.suggested_bid == 21000 and car.auction_fee == 777
        assert car.attempts == 2 and car.is_checked is True


async def test_task_uses_expenses_changed_during_parser_call_for_suggested_bid(database, monkeypatch):
    from tasks import task

    seed_pricing(database)
    with database.sessions() as db:
        db.add(
            CarModel(
                vin="live-costs", vehicle="car", attempts=1, auction="Copart", transportation=500, parts_cost=1000
            )
        )
        db.commit()
    monkeypatch.setattr(task, "SessionLocal", database.sessions)

    def fetch(*args, **kwargs):
        with database.sessions() as other:
            other.scalar(select(CarModel)).transportation = 1500
            other.commit()
        return httpx.Response(
            200,
            request=httpx.Request("GET", "http://local/parser"),
            json={"jd": 20000, "d_max": 20000, "manheim": 20000},
        )

    monkeypatch.setattr(task, "http_get_with_retries", fetch)
    assert task.parse_and_update_car(vin="live-costs")["status"] == "success"
    with database.sessions() as db:
        car = db.scalar(select(CarModel))
        assert car.predicted_total_investments == 16000 and car.auction_fee == 900
        assert car.transportation == 1500 and car.suggested_bid == 12600
