from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select

from crud.fee_import import replace_iaai_fees
from domain.iaai_fees import FeeImage, IaaiFeeResponse, iaai_fee_bands
from models.vehicle import FeeModel
from services.fee_import import refresh_iaai_fees

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
IMAGE = FeeImage("image.png", b"content", "image/png")


async def seed_fees(sessions):
    async with sessions() as db:
        db.add_all(
            [
                FeeModel(auction=auction, fee_type="old", amount=1, percent=False)
                for auction in ("iaai", "copart", "IAAI")
            ]
        )
        await db.commit()


async def assert_original(sessions):
    async with sessions() as db:
        rows = (await db.execute(select(FeeModel))).scalars().all()
        assert {(row.auction, row.fee_type, row.amount) for row in rows} == {
            (auction, "old", 1) for auction in ("iaai", "copart", "IAAI")
        }


@pytest.mark.parametrize("status,data", [(200, {}), (500, {"detail": "scraping error"})])
async def test_defaults_and_error_status_commit_only_exact_iaai_rows(postgres_sessions, status, data):
    await seed_fees(postgres_sessions)
    source = AsyncMock()
    source.fetch.return_value = IaaiFeeResponse(status, data)
    async with postgres_sessions() as db:
        result = await refresh_iaai_fees(db, source, IMAGE, IMAGE)
        assert (result.status, result.data) == (status, data)
    async with postgres_sessions() as db:
        rows = (await db.execute(select(FeeModel))).scalars().all()
        assert {(row.auction, row.fee_type) for row in rows if row.auction != "iaai"} == {
            ("copart", "old"),
            ("IAAI", "old"),
        }
        assert {(row.fee_type, row.amount, row.price_from, row.price_to) for row in rows if row.auction == "iaai"} == {
            ("service_fee", 95, 0, 1000000),
            ("environmental_fee", 15, 0, 1000000),
            ("title_handling_fee", 20, 0, 1000000),
        }


@pytest.mark.parametrize("failure", ["negative", "commit", "insert"])
async def test_replacement_failure_restores_old_fees_and_discards_new_rows(postgres_sessions, monkeypatch, failure):
    await seed_fees(postgres_sessions)
    source = AsyncMock()
    data = {"fees": {"title_handling_fee": {"amount": -1}}} if failure == "negative" else {}
    source.fetch.return_value = IaaiFeeResponse(200, data)
    engine = postgres_sessions.kw["bind"].sync_engine

    def fail_insert(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO fees"):
            raise RuntimeError("fee insert failed")

    if failure == "insert":
        event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        async with postgres_sessions() as db:
            if failure == "commit":
                monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
            with pytest.raises((ValueError, RuntimeError)):
                await refresh_iaai_fees(db, source, IMAGE, IMAGE)
    finally:
        if failure == "insert":
            event.remove(engine, "before_cursor_execute", fail_insert)
    await assert_original(postgres_sessions)


async def test_repository_leaves_transaction_to_caller(postgres_sessions):
    await seed_fees(postgres_sessions)
    async with postgres_sessions() as db:
        await replace_iaai_fees(db, iaai_fee_bands({}))
        await db.flush()
        assert len((await db.execute(select(FeeModel).where(FeeModel.auction == "iaai"))).scalars().all()) == 3
        await assert_original(postgres_sessions)
        await db.rollback()
    await assert_original(postgres_sessions)
