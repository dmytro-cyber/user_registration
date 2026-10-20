from unittest.mock import Mock

import pytest
from sqlalchemy import select

from crud.fee_import import replace_copart_fees
from db.config import DatabaseConfig
from db.factories import create_sync_database
from domain.fee_import import copart_fee_bands
from models.vehicle import FeeModel
from services.fee_import import refresh_copart_fees

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def sync_database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


async def seed_fees(sessions):
    async with sessions() as db:
        db.add_all(
            [
                FeeModel(auction=auction, fee_type="old", amount=1, percent=False, price_from=0, price_to=1000000)
                for auction in ("copart", "iaai", "Copart")
            ]
        )
        await db.commit()


def source(fees):
    return Mock(fetch=Mock(return_value={"fees": {"copart": {"fees": fees}}}))


async def assert_original_fees(sessions):
    async with sessions() as db:
        rows = (await db.execute(select(FeeModel))).scalars().all()
        assert {(row.auction, row.fee_type, row.amount) for row in rows} == {
            (auction, "old", 1) for auction in ("copart", "iaai", "Copart")
        }


async def test_replaces_only_exact_copart_rows_with_percent_and_fixed_bands(sync_database, postgres_sessions):
    await seed_fees(postgres_sessions)
    feed = source(
        {
            "bidding_fees": {"secured": {"0-100": {"amount": 5, "percent": True}}},
            "virtual_bid_fee": {"live_bid": {"8000+": 20}},
            "gate_fee": {"amount": 95, "percent": True},
            "environmental_fee": {"amount": 15},
        }
    )
    with sync_database.sessions() as db:
        assert refresh_copart_fees(db, feed) == {"status": "success"}
    async with postgres_sessions() as db:
        rows = (await db.execute(select(FeeModel))).scalars().all()
        assert {(row.auction, row.fee_type) for row in rows if row.auction != "copart"} == {
            ("iaai", "old"),
            ("Copart", "old"),
        }
        assert {
            (row.fee_type, row.amount, row.percent, row.price_from, row.price_to)
            for row in rows
            if row.auction == "copart"
        } == {
            ("bidding_fees", 5, True, 0, 100),
            ("virtual_bid_fee", 20, False, 8000, 1000000),
            ("gate_fee", 95, False, 0, 1000000),
            ("environmental_fee", 15, False, 0, 1000000),
        }


async def test_empty_valid_table_retains_existing_clear_policy(sync_database, postgres_sessions):
    await seed_fees(postgres_sessions)
    with sync_database.sessions() as db:
        assert refresh_copart_fees(db, source({})) == {"status": "success"}
    async with postgres_sessions() as db:
        assert set((await db.execute(select(FeeModel.auction))).scalars()) == {"iaai", "Copart"}


@pytest.mark.parametrize("failure", ["invalid_amount", "negative_amount", "commit", "source", "missing_payload"])
async def test_failures_restore_deleted_tariffs_and_discard_partial_replacement(
    sync_database, postgres_sessions, monkeypatch, failure
):
    await seed_fees(postgres_sessions)
    amount = "bad" if failure == "invalid_amount" else -5 if failure == "negative_amount" else 10
    feed = source({"bidding_fees": {"secured": {"0-100": 5, "100-200": amount}}})
    if failure == "source":
        feed.fetch.side_effect = RuntimeError("upstream failed")
    elif failure == "missing_payload":
        feed.fetch.return_value = {"error": "upstream error"}
    with sync_database.sessions() as db:
        if failure == "commit":
            monkeypatch.setattr(db, "commit", Mock(side_effect=RuntimeError("commit failed")))
        with pytest.raises((ValueError, RuntimeError, KeyError)):
            refresh_copart_fees(db, feed)
    await assert_original_fees(postgres_sessions)


async def test_repository_does_not_commit_replacement(sync_database, postgres_sessions):
    await seed_fees(postgres_sessions)
    with sync_database.sessions() as db:
        replace_copart_fees(db, copart_fee_bands({"gate_fee": {"amount": 95}}))
        db.flush()
        assert db.execute(select(FeeModel).where(FeeModel.auction == "copart")).scalar_one().fee_type == "gate_fee"
        await assert_original_fees(postgres_sessions)
        db.rollback()
    await assert_original_fees(postgres_sessions)
