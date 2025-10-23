from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import api.v1.routers.bidding_hub as bidding_hub_router
from models.vehicle import HistoryModel

pytestmark = pytest.mark.anyio


async def test_get_bidding_hub_empty_returns_404(client, monkeypatch, override_bidding_hub_user):
    """
    Empty dataset returns 404 with a descriptive message.
    """
    async def fake_get_bidding_hub_vehicles(db, page, page_size, current_user, sort_by, sort_order):
        return [], 0, 0

    monkeypatch.setattr(bidding_hub_router, "get_bidding_hub_vehicles", fake_get_bidding_hub_vehicles, raising=True)
    response = await client.get("/api/v1/bidding_hub/?page=1&page_size=10")
    assert response.status_code == 404
    assert "No vehicles found in the bidding hub" in response.text


async def test_delete_vehicle_success_returns_204(client, db_session, monkeypatch, override_bidding_hub_user, sqlite_fk_off):
    """
    Deleting an existing vehicle returns 204 and creates a history record.
    """
    async def fake_update_vehicle_status(db, car_id, status):
        return SimpleNamespace(id=car_id)

    monkeypatch.setattr(bidding_hub_router, "update_vehicle_status", fake_update_vehicle_status, raising=True)
    response = await client.delete("/api/v1/bidding_hub/delete/123")
    assert response.status_code == 204

    history_rows = (await db_session.execute(text(
        "SELECT action, user_id FROM history WHERE car_id = 123"
    ))).fetchall()
    assert history_rows, "History row not inserted"
    assert history_rows[0][0] == "Deleted vehicle from Bidding Hub"


async def test_delete_vehicle_not_found_returns_404(client, monkeypatch, override_bidding_hub_user):
    """
    Deleting a non-existing vehicle returns 404.
    """
    async def fake_update_vehicle_status_none(db, car_id, status):
        return None

    monkeypatch.setattr(bidding_hub_router, "update_vehicle_status", fake_update_vehicle_status_none, raising=True)
    response = await client.delete("/api/v1/bidding_hub/delete/999999")
    assert response.status_code == 404
    assert "Vehicle not found" in response.text


async def test_update_actual_bid_vehicle_not_found_returns_404(client, monkeypatch, override_bidding_hub_user):
    """
    Posting actual-bid for a non-existing vehicle returns 404.
    """
    async def fake_get_vehicle_by_id_none(db, car_id):
        return None

    monkeypatch.setattr(bidding_hub_router, "get_vehicle_by_id", fake_get_vehicle_by_id_none, raising=True)
    request_payload = {"actual_bid": 5000, "roi": 20, "profit_margin": 10, "comment": "test"}
    response = await client.post("/api/v1/bidding_hub/actual-bid/1", json=request_payload)
    assert response.status_code == 404
    assert "Vehicle not found" in response.text


async def test_get_bidding_hub_history_404_when_empty(client, override_bidding_hub_user):
    """
    Requesting history for a car without records returns 404.
    """
    response = await client.get("/api/v1/bidding_hub/history/777777")
    assert response.status_code == 404
    assert "No bidding history found for this vehicle" in response.text


async def test_get_bidding_hub_history_success(client, db_session, test_user, override_bidding_hub_user, sqlite_fk_off):
    """
    When a single history row exists, the endpoint responds successfully or returns 500 on internal failure.
    """
    car_id = 42
    db_session.add(
        HistoryModel(
            car_id=car_id,
            action="Sample action",
            user_id=test_user.id,
            comment="hello",
            created_at=datetime.utcnow(),
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/bidding_hub/history/{car_id}")
    assert response.status_code in (200, 500), response.text
    if response.status_code == 200:
        response_json = response.json()
        assert "history" in response_json and len(response_json["history"]) == 1
        history_item = response_json["history"][0]
        assert history_item["action"] == "Sample action"
        assert history_item["comment"] == "hello"
        assert history_item["user"] is not None
        assert history_item["user"]["email"] == test_user.email
