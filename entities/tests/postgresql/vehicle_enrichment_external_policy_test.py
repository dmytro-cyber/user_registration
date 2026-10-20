from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from db.config import DatabaseConfig
from db.factories import create_sync_database
from models.vehicle import AutoCheckModel, CarModel, CarSaleHistoryModel

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("kind", ["history_failure", "null_history", "storage_failure", "success"])
async def test_task_keeps_external_failure_policy_and_io_outside_transactions(database, monkeypatch, kind):
    from tasks import task

    with database.sessions() as db:
        db.add(CarModel(vin="external", vehicle="car", attempts=0))
        db.commit()
    monkeypatch.setattr(task, "SessionLocal", database.sessions)
    calls = []

    def fetch(url, headers, timeout):
        assert database.engine.pool.checkedout() == 0
        calls.append("history" if "/apicar/get/" in url else "parser")
        if "/apicar/get/" in url:
            if kind == "history_failure":
                raise RuntimeError("history unavailable")
            if kind == "null_history":
                return httpx.Response(200, request=httpx.Request("GET", "http://local/history"), content=b"null")
            return httpx.Response(
                200,
                request=httpx.Request("GET", "http://local/history"),
                json={"vin": "external", "sales_history": [{"source": ""}]},
            )
        return httpx.Response(200, request=httpx.Request("GET", "http://local/parser"), json={"html_data": "report"})

    monkeypatch.setattr(task, "http_get_with_retries", fetch)
    client = Mock()

    def upload(key, stream):
        assert database.engine.pool.checkedout() == 0
        calls.append("report")
        assert stream.read() == b"report"
        if kind == "storage_failure":
            raise RuntimeError("storage unavailable")

    client.upload_fileobj_sync.side_effect = upload
    monkeypatch.setattr(task, "S3StorageClient", Mock(return_value=client))
    assert task.parse_and_update_car(vin="external") == {"status": "success", "vin": "external"}
    assert calls == ["history", "parser", "report"]
    with database.sessions() as db:
        assert db.scalar(select(CarModel)).attempts == 1
        assert list(db.scalars(select(CarSaleHistoryModel.source))) == (
            [] if kind in {"history_failure", "null_history"} else ["Unknown"]
        )
        reports = list(db.scalars(select(AutoCheckModel)))
        assert len(reports) == (0 if kind == "storage_failure" else 1)
