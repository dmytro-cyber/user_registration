from types import SimpleNamespace

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from models.admin import FilterModel, ROIModel
from models.vehicle import FeeModel

pytestmark = pytest.mark.anyio


@pytest.mark.integration
async def test_create_filter_success(client: AsyncClient, db_session: AsyncSession, patch_admin_module, as_admin):
    """
    Creating a filter as admin returns 201 and persists the record.
    """
    payload = {
        "make": "Toyota",
        "model": "Camry",
        "year_from": 2015,
        "year_to": 2020,
        "odometer_min": 0,
        "odometer_max": 200000,
    }
    response = await client.post("/api/v1/admin/filters", json=payload)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    response_json = response.json()
    assert response_json["make"] == "Toyota"

    stored_filter = (await db_session.execute(
        select(FilterModel).where(FilterModel.id == response_json["id"])
    )).scalars().first()
    assert stored_filter is not None


@pytest.mark.integration
async def test_create_filter_lock_conflict(client: AsyncClient, patch_admin_module_lock_busy, as_admin):
    """
    Creating a filter while kickoff lock is held returns 409.
    """
    payload = {
        "make": "Ford",
        "year_from": 2010,
        "year_to": 2012,
        "odometer_min": 0,
        "odometer_max": 100000,
    }
    response = await client.post("/api/v1/admin/filters", json=payload)
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "Previous kickoff task is still running" in response.text


@pytest.mark.integration
async def test_get_filters_list(client: AsyncClient, db_session: AsyncSession, patch_admin_module):
    """
    Fetching filters returns the list containing the inserted item.
    """
    filter_obj = FilterModel(make="BMW", model="3 Series", year_from=2012, year_to=2018, odometer_min=10, odometer_max=150000)
    db_session.add(filter_obj)
    await db_session.commit()

    response = await client.get("/api/v1/admin/filters")
    assert response.status_code == 200
    response_list = response.json()
    assert any(item["make"] == "BMW" for item in response_list)


@pytest.mark.integration
async def test_get_filter_by_id_ok(client: AsyncClient, db_session: AsyncSession):
    """
    Getting a filter by id returns 200 and the expected object.
    """
    filter_obj = FilterModel(make="Audi", model="A4", year_from=2014, year_to=2019, odometer_min=0, odometer_max=200000)
    db_session.add(filter_obj)
    await db_session.commit()
    await db_session.refresh(filter_obj)

    response = await client.get(f"/api/v1/admin/filters/{filter_obj.id}")
    assert response.status_code == 200
    assert response.json()["id"] == filter_obj.id


@pytest.mark.integration
async def test_get_filter_by_id_404(client: AsyncClient):
    """
    Getting a non-existing filter id returns 404.
    """
    response = await client.get("/api/v1/admin/filters/999999")
    assert response.status_code == 404


@pytest.mark.integration
async def test_update_filter_and_relevance(client: AsyncClient, db_session: AsyncSession, monkeypatch, as_admin):
    """
    Updating a filter returns 200 and triggers subsequent relevance update flow.
    """
    from api.v1.routers import admin as admin_router_mod
    monkeypatch.setattr(admin_router_mod, "is_kickoff_busy", lambda: False, raising=True)
    monkeypatch.setattr(admin_router_mod, "set_kickoff_lock", lambda _id: None, raising=True)
    monkeypatch.setattr(
        admin_router_mod.celery_app, "send_task", lambda *a, **k: SimpleNamespace(id="fake-task-id"), raising=True
    )
    monkeypatch.setattr(admin_router_mod, "build_car_filter_query", lambda _filter: [], raising=True)

    filter_obj = FilterModel(make="Kia", model="Soul", year_from=2016, year_to=2018, odometer_min=0, odometer_max=100000)
    db_session.add(filter_obj)
    await db_session.commit()
    await db_session.refresh(filter_obj)

    payload = {"year_to": 2020}
    response = await client.patch(f"/api/v1/admin/filters/{filter_obj.id}", json=payload)
    assert response.status_code == 200
    assert "Filter updated" in response.text


@pytest.mark.integration
async def test_delete_filter_404(client: AsyncClient):
    """
    Deleting a non-existing filter may return 401/403 before reaching 404.
    """
    response = await client.delete("/api/v1/admin/filters/123456")
    assert response.status_code in (401, 403, 404)


@pytest.mark.integration
async def test_delete_filter_ok(client: AsyncClient, db_session: AsyncSession, as_admin):
    """
    Deleting an existing filter returns 204 and removes it from DB.
    """
    filter_obj = FilterModel(make="Mazda", model="3", year_from=2010, year_to=2013, odometer_min=0, odometer_max=180000)
    db_session.add(filter_obj)
    await db_session.commit()
    await db_session.refresh(filter_obj)

    response = await client.delete(f"/api/v1/admin/filters/{filter_obj.id}")
    assert response.status_code == 204

    deleted = (await db_session.execute(select(FilterModel).where(FilterModel.id == filter_obj.id))).scalars().first()
    assert deleted is None


@pytest.mark.integration
async def test_get_roi_empty_404(client: AsyncClient):
    """
    When there is no ROI in DB, listing returns 404.
    """
    response = await client.get("/api/v1/admin/roi")
    assert response.status_code == 404


@pytest.mark.integration
async def test_create_roi_and_fetch(client: AsyncClient, db_session: AsyncSession):
    """
    Creating ROI returns 201; latest and list endpoints return the created record.
    """
    create_response = await client.post("/api/v1/admin/roi", json={"roi": 0.25})
    assert create_response.status_code == status.HTTP_201_CREATED, create_response.text
    created_roi = create_response.json()
    assert created_roi["roi"] == 0.25

    latest_response = await client.get("/api/v1/admin/roi/latest")
    assert latest_response.status_code == 200
    assert latest_response.json()["roi"] == 0.25

    list_response = await client.get("/api/v1/admin/roi")
    assert list_response.status_code == 200
    roi_list = list_response.json()["roi"]
    assert len(roi_list) >= 1
    assert any(item["roi"] == 0.25 for item in roi_list)


@pytest.mark.integration
async def test_load_db(client: AsyncClient, monkeypatch):
    """
    Load-db endpoint responds with a success code when the scraper stub is used.
    """
    import httpx

    import api.v1.routers.admin as admin_router_mod

    class _CtxAsyncClient:
        def __init__(self, *args, **kwargs): ...
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): ...
        async def post(self, *args, **kwargs):
            class _Resp:
                status_code = 200
                text = '{"ok": true}'
                def json(self): return {"ok": True}
            return _Resp()

    monkeypatch.setattr(admin_router_mod, "httpx", httpx, raising=False)
    monkeypatch.setattr(admin_router_mod.httpx, "AsyncClient", _CtxAsyncClient, raising=True)

    response = await client.post("/api/v1/admin/load-db")
    assert response.status_code in (200, 202, 204)
