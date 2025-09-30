import io
import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.dependencies import get_current_user
from main import app
from models.admin import FilterModel, ROIModel
from models.user import UserModel, UserRoleModel, UserRoleEnum
from models.vehicle import FeeModel
from fastapi import status

pytestmark = pytest.mark.anyio


class _MockHTTPResponse:
    def __init__(self, status_code: int, data: Dict[str, Any]):
        self.status_code = status_code
        self._json = data
        self.text = json.dumps(data)

    def json(self) -> Dict[str, Any]:
        return self._json


class _MockAsyncClient:
    """
    Повна підміна httpx.AsyncClient для використання у 'async with' контексті,
    повертає зашиті відповіді в залежності від URL.
    """
    def __init__(self, *_, **__):
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._closed = True

    async def post(self, url: str, **kwargs):
        if url.endswith("/api/v1/parsers/scrape/iaai/fees"):
            fake = {
                "source": "iaai",
                "payment_method": "standard",
                "fees": {
                    "high_volume_buyer_fees": {
                        "fees": {
                            "0.00-99.99": "25.00",
                            "100.00-199.99": "50.00",
                            "15000.00+": "2% of sale price"
                        }
                    },
                    "internet_bid_buyer_fees": {
                        "fees": {
                            "0.00-99.99": "5.00",
                            "100.00-199.99": "10.00",
                            "15000.00+": "1.5% of sale price"
                        }
                    },
                    "service_fee": {"amount": 95.0, "currency": "USD"},
                    "environmental_fee": {"amount": 15.0, "currency": "USD"},
                    "title_handling_fee": {"amount": 20.0, "currency": "USD"},
                },
                "scraped_at": "2025-01-01 00:00:00",
            }
            return _MockHTTPResponse(200, fake)

        if url.endswith("/startup"):
            return _MockHTTPResponse(200, {"ok": True})

        # За замовчуванням
        return _MockHTTPResponse(404, {"detail": "not mocked"})


# ----------------------------
# /admin/filters
# ----------------------------
@pytest.fixture
def as_admin():
    class _Role: name = UserRoleEnum.ADMIN
    class _Admin:
        id = 777
        email = "admin@example.com"
        is_admin = True
        role = _Role()
        roles = [UserRoleEnum.ADMIN]
        scopes = ["admin"]
    app.dependency_overrides[get_current_user] = lambda: _Admin()
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def _patch_admin_module(monkeypatch):
    """
    Уніфікований патч для:
    - блокувань/локів (щоб не чіпати Redis),
    - celery_app.send_task (щоб нічого не відправляти реально).
    """
    # Імпортуємо сам модуль, де оголошені роути (щоб патчити саме у ньому)
    from api.v1.routers import admin as admin_router_mod

    # не зайнятий лок
    monkeypatch.setattr(admin_router_mod, "is_kickoff_busy", lambda: False, raising=True)
    # no-op для запису лока
    monkeypatch.setattr(admin_router_mod, "set_kickoff_lock", lambda _id: None, raising=True)
    # мок celery
    monkeypatch.setattr(
        admin_router_mod.celery_app,
        "send_task",
        lambda *a, **k: SimpleNamespace(id="fake-task-id"),
        raising=True,
    )


@pytest.fixture
def _patch_admin_module_lock_busy(monkeypatch):
    """Варіант — коли лок зайнятий (для перевірки 409)."""
    from api.v1.routers import admin as admin_router_mod
    monkeypatch.setattr(admin_router_mod, "is_kickoff_busy", lambda: True, raising=True)


@pytest.mark.integration
async def test_create_filter_success(client: AsyncClient, db_session: AsyncSession, _patch_admin_module, as_admin):
    payload = {
        "make": "Toyota",
        "model": "Camry",
        "year_from": 2015,
        "year_to": 2020,
        "odometer_min": 0,
        "odometer_max": 200000,
    }
    r = await client.post("/api/v1/admin/filters", json=payload)
    assert r.status_code == status.HTTP_201_CREATED, r.text
    data = r.json()
    assert data["make"] == "Toyota"

    # перевіримо, що зʼявився запис
    obj = (await db_session.execute(select(FilterModel).where(FilterModel.id == data["id"]))).scalars().first()
    assert obj is not None


@pytest.mark.integration
async def test_create_filter_lock_conflict(client: AsyncClient, _patch_admin_module_lock_busy, as_admin):
    payload = {
        "make": "Ford",
        "year_from": 2010,
        "year_to": 2012,
        "odometer_min": 0,
        "odometer_max": 100000,
    }
    r = await client.post("/api/v1/admin/filters", json=payload)
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "Previous kickoff task is still running" in r.text


@pytest.mark.integration
async def test_get_filters_list(client: AsyncClient, db_session: AsyncSession, _patch_admin_module):
    # створимо один фільтр напряму
    f = FilterModel(make="BMW", model="3 Series", year_from=2012, year_to=2018, odometer_min=10, odometer_max=150000)
    db_session.add(f)
    await db_session.commit()

    r = await client.get("/api/v1/admin/filters")
    assert r.status_code == 200
    data = r.json()
    assert any(x["make"] == "BMW" for x in data)


@pytest.mark.integration
async def test_get_filter_by_id_ok(client: AsyncClient, db_session: AsyncSession):
    f = FilterModel(make="Audi", model="A4", year_from=2014, year_to=2019, odometer_min=0, odometer_max=200000)
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)

    r = await client.get(f"api/v1/admin/filters/{f.id}")
    assert r.status_code == 200
    assert r.json()["id"] == f.id


@pytest.mark.integration
async def test_get_filter_by_id_404(client: AsyncClient):
    r = await client.get("api/v1/admin/filters/999999")
    assert r.status_code == 404


@pytest.mark.integration
async def test_update_filter_and_relevance(client: AsyncClient, db_session: AsyncSession, monkeypatch, as_admin):
    # Підміняємо локи / celery
    from api.v1.routers import admin as admin_router_mod
    monkeypatch.setattr(admin_router_mod, "is_kickoff_busy", lambda: False, raising=True)
    monkeypatch.setattr(admin_router_mod, "set_kickoff_lock", lambda _id: None, raising=True)
    monkeypatch.setattr(
        admin_router_mod.celery_app,
        "send_task",
        lambda *a, **k: SimpleNamespace(id="fake-task-id"),
        raising=True,
    )
    # Спростимо build_car_filter_query, щоб не чіпати реальні умови
    monkeypatch.setattr(
        admin_router_mod,
        "build_car_filter_query",
        lambda _filter: [],
        raising=True,
    )

    f = FilterModel(make="Kia", model="Soul", year_from=2016, year_to=2018, odometer_min=0, odometer_max=100000)
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)

    payload = {"year_to": 2020}
    r = await client.patch(f"api/v1/admin/filters/{f.id}", json=payload)
    assert r.status_code == 200
    assert "Filter updated" in r.text


@pytest.mark.integration
async def test_delete_filter_404(client: AsyncClient):
    r = await client.delete("api/v1/admin/filters/123456")
    assert r.status_code in (401, 403, 404)
    # без override користувача залежність може дати 401/403 до того як дійде до 404


@pytest.fixture
def override_current_user():
    from core.dependencies import get_current_user

    class _Dummy:
        id = 777
        email = "dummy@example.com"

    app.dependency_overrides[get_current_user] = lambda: _Dummy()
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.integration
async def test_delete_filter_ok(client: AsyncClient, db_session: AsyncSession, override_current_user, as_admin):
    # створимо фільтр
    f = FilterModel(make="Mazda", model="3", year_from=2010, year_to=2013, odometer_min=0, odometer_max=180000)
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)

    r = await client.delete(f"api/v1/admin/filters/{f.id}")
    # 204 No Content
    assert r.status_code == 204

    # переконаємось що видалений
    again = (await db_session.execute(select(FilterModel).where(FilterModel.id == f.id))).scalars().first()
    assert again is None


# ----------------------------
# ROI endpoints
# ----------------------------

@pytest.mark.integration
async def test_get_roi_empty_404(client: AsyncClient):
    r = await client.get("api/v1/admin/roi")
    assert r.status_code == 404


@pytest.mark.integration
async def test_create_roi_and_fetch(client: AsyncClient, db_session: AsyncSession):
    # створити
    r = await client.post("api/v1/admin/roi", json={"roi": 0.25})
    assert r.status_code == status.HTTP_201_CREATED, r.text
    data = r.json()
    assert data["roi"] == 0.25

    # latest
    r2 = await client.get("api/v1/admin/roi/latest")
    assert r2.status_code == 200
    assert r2.json()["roi"] == 0.25

    # list
    r3 = await client.get("api/v1/admin/roi")
    assert r3.status_code == 200
    lst = r3.json()["roi"]
    assert len(lst) >= 1
    assert any(item["roi"] == 0.25 for item in lst)


# ----------------------------
# IAAI fees proxy (no real HTTP)
# ----------------------------

@pytest.fixture
def patch_httpx_client(monkeypatch):
    # Підмінимо httpx.AsyncClient на нашу фейкову реалізацію
    import api.v1.routers.admin as admin_router_mod
    monkeypatch.setattr(admin_router_mod.httpx, "AsyncClient", _MockAsyncClient, raising=True)


@pytest.mark.integration
async def test_upload_iaai_fees(client: AsyncClient, db_session: AsyncSession, patch_httpx_client):
    # згенеруємо два прості файли (svg або png — не важливо для тесту, бо не відправляємо назовні)
    files = {
        "high_volume": ("high.svg", b"<svg/>", "image/svg+xml"),
        "internet_bid": ("internet.svg", b"<svg/>", "image/svg+xml"),
    }
    r = await client.post("api/v1/admin/upload-iaai-fees", files=files)
    assert r.status_code == 200, r.text

    # перевіримо, що збори записалися у БД
    rows = (await db_session.execute(select(FeeModel).where(FeeModel.auction == "iaai"))).scalars().all()
    assert len(rows) > 0

    # швидкі sanity-перевірки: є фіксовані і діапазонні
    assert any(f.fee_type == "service_fee" and not f.percent for f in rows)
    assert any(f.fee_type == "internet_bid_buyer_fees" for f in rows)


# ----------------------------
# Loader (no real HTTP)
# ----------------------------

@pytest.mark.integration
async def test_load_db(client: AsyncClient, monkeypatch):
    # Підмінимо httpx.AsyncClient
    import api.v1.routers.admin as admin_router_mod
    monkeypatch.setattr(admin_router_mod.httpx, "AsyncClient", _MockAsyncClient, raising=True)

    r = await client.post("api/v1/admin/load-db")
    assert r.status_code == 200 or r.status_code == 204 or r.status_code == 202
