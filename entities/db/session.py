"""Async database ownership and session cleanup. Application services own commits."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from db.config import DatabaseConfig
from db.factories import AsyncDatabase, create_async_database

_database: AsyncDatabase | None = None


def get_database() -> AsyncDatabase:
    global _database
    if _database is None:
        from core.config import settings

        _database = create_async_database(DatabaseConfig.from_settings(settings))
    return _database


def SessionLocal() -> AsyncSession:
    """Compatibility entrypoint for code that needs a session factory."""
    return get_database().sessions()


@asynccontextmanager
async def session_scope(factory: Callable[[], AsyncSession] | None = None) -> AsyncIterator[AsyncSession]:
    async with (factory or SessionLocal)() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


async def dispose_database() -> None:
    global _database
    database, _database = _database, None
    if database is not None:
        await database.engine.dispose()


@asynccontextmanager
async def database_lifespan(app):
    try:
        yield
    finally:
        await dispose_database()
