from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

import api.v1.routers.bidding_hub as bidding_hub_router
from models.vehicle import CarModel, CarStatus, FeeModel, HistoryModel

pytestmark = pytest.mark.anyio


async def test_get_bidding_hub_empty_returns_404(client, override_bidding_hub_user):
    response = await client.get("/api/v1/bidding_hub/?page=1&page_size=10")
    assert response.status_code == 404
    assert response.json() == {"detail": "No vehicles found in the bidding hub"}


async def test_delete_vehicle_success_returns_204(client, db_session, create_car, test_user, use_test_user):
    car = await create_car()
    car.car_status = CarStatus.BIDDING
    await db_session.commit()
    response = await client.delete(f"/api/v1/bidding_hub/delete/{car.id}")
    assert response.status_code == 204 and response.content == b""
    await db_session.refresh(car)
    assert car.car_status == CarStatus.DELETED_FROM_BIDDING_HUB
    histories = (await db_session.execute(select(HistoryModel).where(HistoryModel.car_id == car.id))).scalars().all()
    assert len(histories) == 1
    assert histories[0].action == "Deleted vehicle from Bidding Hub" and histories[0].user_id == test_user.id


async def test_delete_vehicle_not_found_returns_404(client, override_bidding_hub_user):
    response = await client.delete("/api/v1/bidding_hub/delete/999999")
    assert response.status_code == 404 and response.json() == {"detail": "Vehicle not found"}


async def test_update_actual_bid_vehicle_not_found_returns_404(client, override_bidding_hub_user):
    """
    Posting actual-bid for a non-existing vehicle returns 404.
    """
    request_payload = {"actual_bid": 5000, "roi": 20, "profit_margin": 10, "comment": "test"}
    response = await client.post("/api/v1/bidding_hub/actual-bid/999999", json=request_payload)
    assert response.status_code == 404
    assert "Vehicle not found" in response.text


@pytest.mark.parametrize("bid", [5000, 6000])
async def test_actual_bid_updates_financials_and_history(
    client, db_session, create_car, test_user, use_test_user, bid,
):
    car = await create_car(
        vin=uuid4().hex[:17], auction=f"Bid{bid}", avg_market_price=12000,
        transportation=100, labor=200, maintenance=300, parts_cost=400,
        auction_fee=9999,
    )
    # Both ends of the fee range are inclusive; auction matching ignores case.
    db_session.add_all([
        FeeModel(auction=f"bid{bid}", fee_type="fixed", price_from=5000, price_to=6000, amount=100, percent=False),
        FeeModel(auction=f"BID{bid}", fee_type="percent", price_from=5000, price_to=6000, amount=10, percent=True),
        FeeModel(auction="IAAI", fee_type="other", price_from=0, price_to=10000, amount=9999, percent=False),
        FeeModel(auction=f"Bid{bid}", fee_type="outside", price_from=6001, price_to=9000, amount=9999, percent=False),
    ])
    await db_session.commit()

    response = await client.post(f"/api/v1/bidding_hub/actual-bid/{car.id}", json={
        "actual_bid": bid, "roi": -999, "profit_margin": -999, "comment": "approved",
    })

    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Current bid updated successfully"}
    await db_session.refresh(car)
    expected_fee = 100 + bid * 0.1
    investment = bid + expected_fee + 1000
    assert car.actual_bid == bid
    assert car.auction_fee == expected_fee
    assert car.profit_margin == 12000 - investment
    assert car.roi == pytest.approx((12000 - investment) / investment * 100)
    history = (await db_session.execute(
        select(HistoryModel).where(HistoryModel.car_id == car.id)
    )).scalars().one()
    assert history.action == f"Updated actual bid from None to {float(bid)}"
    assert history.user_id == test_user.id
    assert history.comment == "approved"


@pytest.mark.parametrize("bid,market_price,expected_margin,expected_roi", [
    (0, 12000, None, None),
    (1000, None, None, None),
    (1000, 0, -1000, -100),
    (1000, 1500, 500, 50),
])
async def test_actual_bid_without_fees(
    client, db_session, create_car, use_test_user,
    bid, market_price, expected_margin, expected_roi,
):
    car = await create_car(vin=uuid4().hex[:17], auction="No fees", avg_market_price=market_price)
    response = await client.post(
        f"/api/v1/bidding_hub/actual-bid/{car.id}", json={"actual_bid": bid},
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(car)
    assert car.auction_fee == 0
    assert car.profit_margin == expected_margin
    assert car.roi == expected_roi


@pytest.mark.parametrize("payload,status_code", [
    ({}, 400),
    ({"actual_bid": None}, 400),
    ({"actual_bid": -1}, 400),
    ({"actual_bid": "invalid"}, 422),
])
async def test_actual_bid_rejects_invalid_input_without_writes(
    client, db_session, create_car, use_test_user, payload, status_code,
):
    car = await create_car(vin=uuid4().hex[:17])
    response = await client.post(f"/api/v1/bidding_hub/actual-bid/{car.id}", json=payload)
    assert response.status_code == status_code, response.text
    if status_code == 400:
        assert response.json() == {"detail": "Actual bid must be a non-negative number"}
    await db_session.refresh(car)
    assert car.actual_bid is None
    assert not (await db_session.execute(
        select(HistoryModel).where(HistoryModel.car_id == car.id)
    )).scalars().all()


async def test_actual_bid_checks_vehicle_before_business_validation(client, use_test_user):
    response = await client.post("/api/v1/bidding_hub/actual-bid/999999", json={"actual_bid": -1})
    assert response.status_code == 404
    assert response.json() == {"detail": "Vehicle not found"}


async def test_actual_bid_requires_authentication(client):
    response = await client.post("/api/v1/bidding_hub/actual-bid/1", json={"actual_bid": 100})
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication credentials were not provided"}


async def test_actual_bid_commit_failure_rolls_back_car_and_history(
    client, db_session, create_car, use_test_user, monkeypatch,
):
    car = await create_car(vin=uuid4().hex[:17], avg_market_price=12000, auction_fee=200)
    car_id = car.id

    async def fail_commit():
        # Flush first to verify rollback undoes both SQL writes.
        await db_session.flush()
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    response = await client.post(
        f"/api/v1/bidding_hub/actual-bid/{car_id}", json={"actual_bid": 1000},
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "Error updating current bid"}
    persisted = (await db_session.execute(select(CarModel).where(CarModel.id == car_id))).scalar_one()
    assert persisted.actual_bid is None
    assert persisted.auction_fee == 200
    assert not (await db_session.execute(
        select(HistoryModel).where(HistoryModel.car_id == car_id)
    )).scalars().all()


async def test_get_bidding_hub_history_404_when_empty(client, override_bidding_hub_user):
    """
    Requesting history for a car without records returns 404.
    """
    response = await client.get("/api/v1/bidding_hub/history/777777")
    assert response.status_code == 404
    assert "No bidding history found for this vehicle" in response.text


async def test_get_bidding_hub_history_success(client, db_session, test_user, override_bidding_hub_user, sqlite_fk_off):
    """
    When a single history row exists, the endpoint returns the serialized user.
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
    assert response.status_code == 200, response.text
    if response.status_code == 200:
        response_json = response.json()
        assert "history" in response_json and len(response_json["history"]) == 1
        history_item = response_json["history"][0]
        assert history_item["action"] == "Sample action"
        assert history_item["comment"] == "hello"
        assert history_item["user"] is not None
        assert history_item["user"]["email"] == test_user.email


@pytest.mark.parametrize("integrity", [False, True])
async def test_delete_vehicle_service_failure_keeps_500(client, monkeypatch, override_bidding_hub_user, integrity):
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import IntegrityError

    error = IntegrityError("INSERT", {}, RuntimeError("failed")) if integrity else RuntimeError("failed")
    monkeypatch.setattr(bidding_hub_router, "remove_vehicle_from_hub_service", AsyncMock(side_effect=error))
    response = await client.delete("/api/v1/bidding_hub/delete/123")
    assert response.status_code == 500 and response.json() == {"detail": "Error deleting vehicle"}


async def test_history_order_null_user_and_vehicle_scope(client, db_session, create_car, use_test_user):
    car = await create_car()
    other = await create_car(vin="HISTORY-OTHER")
    db_session.add_all([
        HistoryModel(car_id=car.id, action="older", created_at=datetime(2020, 1, 1)),
        HistoryModel(car_id=car.id, action="newer", comment="note", created_at=datetime(2021, 1, 1)),
        HistoryModel(car_id=other.id, action="excluded"),
    ])
    await db_session.commit()
    response = await client.get(f"/api/v1/bidding_hub/history/{car.id}")
    assert response.status_code == 200, response.text
    rows = response.json()["history"]
    assert [row["action"] for row in rows] == ["newer", "older"]
    assert all(row["user"] is None for row in rows)
    assert rows[0]["comment"] == "note" and rows[1]["comment"] is None


async def test_history_incomplete_user_keeps_500(client, db_session, create_car, test_user, use_test_user):
    car = await create_car()
    test_user.first_name = None
    db_session.add(HistoryModel(car_id=car.id, action="incomplete", user_id=test_user.id))
    await db_session.commit()
    response = await client.get(f"/api/v1/bidding_hub/history/{car.id}")
    assert response.status_code == 500 and response.json() == {"detail": "Error fetching bidding history"}


async def test_history_query_failure_keeps_500(client, monkeypatch, override_bidding_hub_user):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(bidding_hub_router, "get_vehicle_bidding_history", AsyncMock(side_effect=RuntimeError("failed")))
    response = await client.get("/api/v1/bidding_hub/history/123")
    assert response.status_code == 500 and response.json() == {"detail": "Error fetching bidding history"}


@pytest.mark.parametrize("query", ["page=0", "page_size=101", "sort_order=wrong"])
async def test_bidding_list_invalid_query_keeps_422(client, override_bidding_hub_user, query):
    response = await client.get(f"/api/v1/bidding_hub/?{query}")
    assert response.status_code == 422


async def test_bidding_list_unknown_sort_keeps_exact_400(client, override_bidding_hub_user):
    response = await client.get("/api/v1/bidding_hub/?sort_by=currnent_bid")
    assert response.status_code == 400
    assert response.json() == {"detail": "Sorting by currnent_bid not alowed"}


async def test_bidding_list_success_and_out_of_range(client, db_session, create_car, use_test_user):
    car = await create_car()
    car.car_status = CarStatus.WON
    await db_session.commit()
    response = await client.get("/api/v1/bidding_hub/?sort_by=vehicle")
    assert response.status_code == 200, response.text
    assert response.json()["total_count"] == response.json()["total_pages"] == 1
    assert response.json()["vehicles"][0]["id"] == car.id
    response = await client.get("/api/v1/bidding_hub/?page=2")
    assert response.status_code == 404 and response.json() == {"detail": "No vehicles found in the bidding hub"}


async def test_bidding_list_failure_keeps_500(client, monkeypatch, override_bidding_hub_user):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(bidding_hub_router, "list_bidding_hub", AsyncMock(side_effect=RuntimeError("failed")))
    response = await client.get("/api/v1/bidding_hub/")
    assert response.status_code == 500 and response.json() == {"detail": "Error fetching bidding hub vehicles"}
