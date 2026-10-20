"""Worker-specific fixtures, independent of the FastAPI application."""

from io import BytesIO

import pytest

import tasks.task as task_module
from tests.support.settings import TestSettings


@pytest.fixture(autouse=False)
def patch_task_sessionlocal(monkeypatch, db_session_sync):
    """
    Patch task_module.SessionLocal to return the test sync session.
    """
    execute = db_session_sync.execute

    def sqlite_execute(statement, *args, **kwargs):
        # SQLite cannot run PostgreSQL timeout commands. These tests cover
        # business logic; row-lock semantics need separate PostgreSQL tests.
        if str(statement).startswith("SET LOCAL "):
            return None
        return execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session_sync, "execute", sqlite_execute)

    class _ContextManager:
        def __enter__(self): return db_session_sync
        def __exit__(self, exc_type, exc, tb): pass

    class _SessionLocalProxy:
        def __call__(self): return _ContextManager()

    monkeypatch.setattr(task_module, "SessionLocal", _SessionLocalProxy())


@pytest.fixture(autouse=False)
def patch_task_settings(monkeypatch):
    """
    Inject TestSettings into the task module.
    """
    settings_obj = TestSettings()
    monkeypatch.setattr(task_module, "settings", settings_obj)
    return settings_obj


@pytest.fixture(autouse=False)
def mock_s3_for_task(monkeypatch):
    """
    Mock S3StorageClient used by the task module.
    """
    upload_calls: list[tuple[str, str]] = []

    class DummyS3Client:
        def __init__(self, **kwargs): ...
        def upload_fileobj_sync(self, key: str, fileobj: BytesIO):
            data_str = fileobj.read().decode("utf-8")
            upload_calls.append((key, data_str))

    monkeypatch.setattr(task_module, "S3StorageClient", DummyS3Client)
    return upload_calls


@pytest.fixture(autouse=False)
def mock_roi_and_fees(monkeypatch):
    """
    Deterministic ROI/fees helpers for task tests.
    """
    class ROIStub:
        roi = 25.0
        profit_margin = 10.0

    def load_default_roi(_db): return ROIStub()
    def load_fees(_db, _auction, _base): return {"stub": True}
    def apply_fees(base_amount, _fees): return round((base_amount or 0.0) * 0.05, 2)

    monkeypatch.setattr(task_module, "_load_default_roi", load_default_roi)
    monkeypatch.setattr(task_module, "_load_fees", load_fees)
    monkeypatch.setattr(task_module, "_apply_fees", apply_fees)


@pytest.fixture(autouse=False)
def http_router_mock(monkeypatch):
    """
    Router for http_get_with_retries used by task module.
    """
    handlers = {"history": None, "parser": None}

    def set_handlers(history_response_callable, parser_response_callable):
        handlers["history"] = history_response_callable
        handlers["parser"] = parser_response_callable

    def router(url, headers, timeout):
        if "/apicar/get/" in url:
            return handlers["history"]()
        return handlers["parser"]()

    monkeypatch.setattr(task_module, "http_get_with_retries", router)
    return set_handlers
