import asyncio

import pytest
from sqlalchemy import select

from db.config import DatabaseConfig
from db.factories import create_async_database
from db.session import session_scope
from models.vehicle import CarModel

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("outcome", ["uncommitted", "exception", "cancelled", "committed"])
async def test_scope_returns_connection_and_only_keeps_explicit_commits(postgres_sessions, outcome):
    config = DatabaseConfig(url=postgres_sessions.kw["bind"].url, pool_size=1, max_overflow=0)
    database = create_async_database(config)
    try:
        assert database.sessions.kw["expire_on_commit"] is False

        async def work():
            async with session_scope(database.sessions) as db:
                db.add(CarModel(vin="SESSION-LIFETIME", vehicle="Test vehicle"))
                await db.flush()
                if outcome == "committed":
                    await db.commit()
                elif outcome == "exception":
                    raise RuntimeError("workflow failed")
                elif outcome == "cancelled":
                    raise asyncio.CancelledError()

        if outcome == "exception":
            with pytest.raises(RuntimeError, match="workflow failed"):
                await work()
        elif outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await work()
        else:
            await work()
        assert database.engine.pool.checkedout() == 0
        async with postgres_sessions() as observer:
            assert (await observer.execute(select(CarModel.vin))).scalars().all() == (
                ["SESSION-LIFETIME"] if outcome == "committed" else []
            )
        async with session_scope(database.sessions) as db:
            await db.execute(select(CarModel))
        assert database.engine.pool.checkedout() == 0
    finally:
        await database.engine.dispose()
