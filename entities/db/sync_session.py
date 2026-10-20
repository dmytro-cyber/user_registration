"""Lazy database ownership for synchronous Celery workflows."""

from sqlalchemy.orm import Session

from db.config import DatabaseConfig
from db.factories import SyncDatabase, create_sync_database

_database: SyncDatabase | None = None


def get_database() -> SyncDatabase:
    global _database
    if _database is None:
        from core.config import settings

        _database = create_sync_database(DatabaseConfig.from_settings(settings))
    return _database


def SessionLocal() -> Session:
    return get_database().sessions()


def dispose_database(*, close: bool = True) -> None:
    global _database
    database, _database = _database, None
    if database is not None:
        database.engine.dispose(close=close)
