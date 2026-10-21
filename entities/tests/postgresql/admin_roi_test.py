from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from crud.roi import add_roi_record
from exceptions.roi import NegativeROIError, ROIRecordNotFound
from models.admin import ROIModel
from services.roi import create_admin_roi, latest_admin_roi, list_admin_roi

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_create_commits_rounded_roi_and_model_margin(postgres_sessions):
    async with postgres_sessions() as db:
        saved = await create_admin_roi(db, 25.004)
        saved_id = saved.id
        assert saved.created_at is not None and saved.roi == 25 and saved.profit_margin == 20
    async with postgres_sessions() as observer:
        saved = await observer.get(ROIModel, saved_id)
        assert saved.roi == 25 and saved.profit_margin == 20


async def test_admin_latest_and_list_use_id_not_created_at_order(postgres_sessions):
    async with postgres_sessions() as db:
        first = ROIModel(roi=10, created_at=datetime(2026, 1, 2))
        db.add(first)
        await db.flush()
        second = ROIModel(roi=25, created_at=datetime(2026, 1, 1))
        db.add(second)
        await db.commit()
        first_id, second_id = first.id, second.id
    async with postgres_sessions() as db:
        assert (await latest_admin_roi(db)).id == second_id
        assert [row.id for row in await list_admin_roi(db)] == [second_id, first_id]


@pytest.mark.parametrize("read", [latest_admin_roi, list_admin_roi])
async def test_empty_read_raises_application_error(postgres_sessions, read):
    async with postgres_sessions() as db:
        with pytest.raises(ROIRecordNotFound):
            await read(db)


async def test_negative_create_does_not_open_transaction_or_commit(postgres_sessions, monkeypatch):
    async with postgres_sessions() as db:
        commit = AsyncMock()
        monkeypatch.setattr(db, "commit", commit)
        with pytest.raises(NegativeROIError):
            await create_admin_roi(db, -0.001)
        commit.assert_not_awaited()
        assert not db.in_transaction()


async def test_commit_failure_rolls_back_new_roi(postgres_sessions, monkeypatch):
    async with postgres_sessions() as db:

        async def fail_commit():
            await db.flush()
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            await create_admin_roi(db, 25)
    async with postgres_sessions() as db:
        assert list((await db.execute(select(ROIModel))).scalars()) == []


async def test_refresh_failure_keeps_already_committed_roi(postgres_sessions, monkeypatch):
    async with postgres_sessions() as db:
        monkeypatch.setattr(db, "refresh", AsyncMock(side_effect=RuntimeError("refresh failed")))
        with pytest.raises(RuntimeError, match="refresh failed"):
            await create_admin_roi(db, 25)
    async with postgres_sessions() as db:
        assert (await latest_admin_roi(db)).roi == 25


async def test_crud_add_does_not_commit(postgres_sessions):
    async with postgres_sessions() as writer, postgres_sessions() as observer:
        record = add_roi_record(writer, 25)
        await writer.flush()
        assert record.profit_margin == 20
        assert list((await observer.execute(select(ROIModel))).scalars()) == []
        await writer.rollback()
    async with postgres_sessions() as db:
        assert list((await db.execute(select(ROIModel))).scalars()) == []
