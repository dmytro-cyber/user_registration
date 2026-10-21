from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from crud.fees import add_fee, apply_fee_fields, remove_fee
from exceptions.fees import FeeNotFound
from models.vehicle import FeeModel
from schemas.vehicle import FeeCreate, FeeUpdate
from services.fees import create_manual_fee, delete_manual_fee, list_manual_fees, update_manual_fee

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def create_payload():
    return FeeCreate(auction="Copart", fee_type="buyer", amount=100.125, price_from=1000, price_to=500)


def update_payload(**changes):
    return FeeUpdate(auction="copart", fee_type="changed", percent=True, **changes)


async def seed(sessions):
    async with sessions() as db:
        fee = FeeModel(auction="Copart", fee_type="buyer", amount=20, percent=False, price_from=0, price_to=500)
        db.add(fee)
        await db.commit()
        return fee.id


async def rows(sessions):
    async with sessions() as db:
        return [
            (f.id, f.auction, f.fee_type, f.amount, f.percent, f.price_from, f.price_to)
            for f in (await db.execute(select(FeeModel).order_by(FeeModel.id))).scalars()
        ]


async def test_create_keeps_precision_case_defaults_and_inverted_range(postgres_sessions):
    async with postgres_sessions() as db:
        created = await create_manual_fee(db, create_payload())
        created_id = created.id
    assert await rows(postgres_sessions) == [(created_id, "Copart", "buyer", 100.125, False, 1000, 500)]


async def test_update_only_explicit_fields_preserves_omissions_and_accepts_zero(postgres_sessions):
    fee_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        await update_manual_fee(db, fee_id, update_payload(price_from=None))
    assert await rows(postgres_sessions) == [(fee_id, "copart", "changed", 20, True, None, 500)]
    async with postgres_sessions() as db:
        await update_manual_fee(db, fee_id, update_payload(amount=0))
    assert (await rows(postgres_sessions))[0][3] == 0


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_missing_fee_raises_application_error_without_commit(postgres_sessions, monkeypatch, operation):
    async with postgres_sessions() as db:
        commit = AsyncMock()
        monkeypatch.setattr(db, "commit", commit)
        with pytest.raises(FeeNotFound):
            if operation == "update":
                await update_manual_fee(db, -1, update_payload())
            else:
                await delete_manual_fee(db, -1)
        commit.assert_not_awaited()


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_commit_failure_rolls_back_operation(postgres_sessions, monkeypatch, operation):
    fee_id = await seed(postgres_sessions)
    before = await rows(postgres_sessions)
    async with postgres_sessions() as db:

        async def fail_commit():
            await db.flush()
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            if operation == "create":
                await create_manual_fee(db, create_payload())
            elif operation == "update":
                await update_manual_fee(db, fee_id, update_payload(amount=50))
            else:
                await delete_manual_fee(db, fee_id)
    assert await rows(postgres_sessions) == before


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_refresh_failure_retains_committed_write(postgres_sessions, monkeypatch, operation):
    fee_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        monkeypatch.setattr(db, "refresh", AsyncMock(side_effect=RuntimeError("refresh failed")))
        with pytest.raises(RuntimeError, match="refresh failed"):
            if operation == "create":
                await create_manual_fee(db, create_payload())
            else:
                await update_manual_fee(db, fee_id, update_payload(amount=50))
    current = await rows(postgres_sessions)
    assert len(current) == (2 if operation == "create" else 1)
    assert current[-1][3] == (100.125 if operation == "create" else 50)


@pytest.mark.parametrize("column,error", [("amount", TypeError), ("auction", IntegrityError), ("percent", IntegrityError)])
async def test_explicit_null_keeps_validation_or_database_failure_and_rollback(postgres_sessions, column, error):
    fee_id = await seed(postgres_sessions)
    before = await rows(postgres_sessions)
    values = dict(auction="changed", fee_type="changed", percent=False)
    values[column] = None
    async with postgres_sessions() as db:
        with pytest.raises(error):
            await update_manual_fee(db, fee_id, FeeUpdate(**values))
    assert await rows(postgres_sessions) == before


async def test_empty_list_is_successful_without_commit(postgres_sessions, monkeypatch):
    async with postgres_sessions() as db:
        commit = AsyncMock()
        monkeypatch.setattr(db, "commit", commit)
        assert await list_manual_fees(db) == []
        commit.assert_not_awaited()


async def test_crud_writes_do_not_commit(postgres_sessions):
    fee_id = await seed(postgres_sessions)
    before = await rows(postgres_sessions)
    async with postgres_sessions() as db:
        fee = await db.get(FeeModel, fee_id)
        apply_fee_fields(fee, {"amount": 50})
        add_fee(db, create_payload().model_dump())
        await remove_fee(db, fee)
        await db.flush()
        assert await rows(postgres_sessions) == before
        await db.rollback()
    assert await rows(postgres_sessions) == before
