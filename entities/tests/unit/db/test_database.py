import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from core.config import Settings
from db import session, sync_session
from db.config import DatabaseConfig
from db.factories import AsyncDatabase, SyncDatabase, create_sync_database


def test_url_keeps_reserved_characters_and_redacts_password():
    settings = Settings(POSTGRES_USER="user@service", POSTGRES_PASSWORD="p@ss:/word%?#", POSTGRES_DB="cars")
    config = DatabaseConfig.from_settings(settings)
    encoded = config.url.render_as_string(hide_password=False)
    assert make_url(encoded).password == settings.POSTGRES_PASSWORD
    assert make_url(encoded).username == settings.POSTGRES_USER
    assert settings.POSTGRES_PASSWORD not in repr(config)
    database = create_sync_database(config)
    try:
        assert database.engine.url.drivername == "postgresql+psycopg2"
        assert database.engine.url.password == settings.POSTGRES_PASSWORD
        assert database.sessions.kw["autoflush"] is False
        assert database.sessions.kw["expire_on_commit"] is True
    finally:
        database.engine.dispose()


@pytest.mark.parametrize(
    "name,value",
    [
        ("DB_POOL_SIZE", "0"),
        ("DB_MAX_OVERFLOW", "-1"),
        ("DB_POOL_TIMEOUT", "0"),
        ("DB_POOL_RECYCLE", "-2"),
        ("DB_ECHO_SQL", "invalid"),
    ],
)
def test_invalid_environment_pool_settings_fail_early(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()


def test_database_modules_import_without_settings_or_engine_creation():
    script = """
import sys
import sqlalchemy
import sqlalchemy.ext.asyncio
def forbidden(*args, **kwargs):
    raise AssertionError('Engine created during import')
sqlalchemy.create_engine = forbidden
sqlalchemy.ext.asyncio.create_async_engine = forbidden
import db.session
import db.sync_session
assert 'core.config' not in sys.modules
assert 'asyncpg' not in sys.modules
assert 'psycopg2' not in sys.modules
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.getcwd()
    subprocess.run([sys.executable, "-c", script], env=environment, check=True, capture_output=True, text=True)


@pytest.mark.asyncio
async def test_async_provider_reuses_engine_and_releases_it_at_lifespan_exit(monkeypatch):
    monkeypatch.setattr(session, "_database", None)
    first = AsyncDatabase(SimpleNamespace(dispose=AsyncMock()), Mock())
    second = AsyncDatabase(SimpleNamespace(dispose=AsyncMock()), Mock())
    create = Mock(side_effect=[first, second])
    monkeypatch.setattr(session, "create_async_database", create)
    async with session.database_lifespan(None):
        assert create.call_count == 0
        assert session.get_database() is session.get_database() is first
    first.engine.dispose.assert_awaited_once()
    assert session.get_database() is second
    await session.dispose_database()
    await session.dispose_database()
    second.engine.dispose.assert_awaited_once()
    assert create.call_count == 2


@pytest.mark.asyncio
async def test_unused_api_lifespan_does_not_create_a_database(monkeypatch):
    monkeypatch.setattr(session, "_database", None)
    create = Mock(side_effect=AssertionError("Unused database created"))
    monkeypatch.setattr(session, "create_async_database", create)
    from main import app

    async with app.router.lifespan_context(app):
        pass
    create.assert_not_called()


def test_sync_worker_fork_discards_pool_without_closing_parent_connections(monkeypatch):
    from core.celery_config import _dispose_worker_database, _initialize_worker_process

    monkeypatch.delenv("CELERY_GEVENT", raising=False)
    parent = SyncDatabase(SimpleNamespace(dispose=Mock()), Mock())
    child = SyncDatabase(SimpleNamespace(dispose=Mock()), Mock())
    monkeypatch.setattr(sync_session, "_database", parent)
    create = Mock(return_value=child)
    monkeypatch.setattr(sync_session, "create_sync_database", create)
    _initialize_worker_process()
    parent.engine.dispose.assert_called_once_with(close=False)
    assert sync_session.get_database() is sync_session.get_database() is child
    _dispose_worker_database()
    _dispose_worker_database()
    child.engine.dispose.assert_called_once_with(close=True)
    create.assert_called_once()
