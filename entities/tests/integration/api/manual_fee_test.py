import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from models.vehicle import FeeModel

pytestmark = pytest.mark.anyio
PAYLOAD = {"auction": "Copart", "fee_type": "buyer", "amount": 100.125, "price_from": 1000, "price_to": 500}


async def test_manual_fee_cycle_keeps_json_and_empty_delete(client, as_admin):
    assert (await client.get("/api/v1/fees/")).json() == []
    created = await client.post("/api/v1/fees/", json=PAYLOAD)
    assert created.status_code == 201
    fee_id = created.json()["id"]
    assert created.json() == {**PAYLOAD, "percent": False, "id": fee_id}
    listed = await client.get("/api/v1/fees/")
    assert listed.status_code == 200 and listed.json() == [created.json()]
    updated = await client.put(
        f"/api/v1/fees/{fee_id}",
        json={"auction": "copart", "fee_type": "changed", "percent": True, "price_from": None},
    )
    assert updated.status_code == 200
    assert updated.json() == {
        **PAYLOAD,
        "id": fee_id,
        "auction": "copart",
        "fee_type": "changed",
        "percent": True,
        "price_from": None,
    }
    deleted = await client.delete(f"/api/v1/fees/{fee_id}")
    assert deleted.status_code == 204 and deleted.content == b""
    assert (await client.get("/api/v1/fees/")).json() == []


async def test_update_retains_required_nullable_fields(client, as_admin):
    response = await client.put("/api/v1/fees/123", json={"amount": 5})
    assert response.status_code == 422
    assert {tuple(error["loc"]) for error in response.json()["detail"]} == {
        ("body", "auction"),
        ("body", "fee_type"),
        ("body", "percent"),
    }


@pytest.mark.parametrize("method", ["put", "delete"])
async def test_missing_fee_keeps_404(client, as_admin, method):
    response = await client.request(
        method,
        "/api/v1/fees/999999",
        **({"json": {"auction": "copart", "fee_type": "buyer", "percent": False}} if method == "put" else {}),
    )
    assert response.status_code == 404 and response.json() == {"detail": "Fee not found"}


@pytest.mark.parametrize("method", ["post", "put"])
async def test_negative_amount_keeps_request_validation(client, as_admin, method):
    response = await client.request(
        method, "/api/v1/fees/" + ("123" if method == "put" else ""), json={**PAYLOAD, "percent": False, "amount": -1}
    )
    assert response.status_code == 422 and response.json()["detail"][0]["loc"] == ["body", "amount"]


async def test_null_percent_keeps_integrity_409_without_persisting(client, as_admin, db_session):
    created = await client.post("/api/v1/fees/", json=PAYLOAD)
    fee_id = created.json()["id"]
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as errors_client:
        response = await errors_client.put(
            f"/api/v1/fees/{fee_id}", json={"auction": "copart", "fee_type": "buyer", "percent": None}
        )
    assert response.status_code == 409 and response.json() == {"detail": "Database integrity error"}
    fee = await db_session.get(FeeModel, fee_id)
    await db_session.refresh(fee)
    assert fee.percent is False and fee.auction == "Copart"


async def test_null_amount_keeps_model_validation_failure_without_persisting(client, as_admin, db_session):
    created = await client.post("/api/v1/fees/", json=PAYLOAD)
    fee_id = created.json()["id"]
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as errors_client:
        response = await errors_client.put(
            f"/api/v1/fees/{fee_id}",
            json={"auction": "changed", "fee_type": "buyer", "percent": False, "amount": None},
        )
    assert response.status_code == 500
    fee = await db_session.get(FeeModel, fee_id)
    await db_session.refresh(fee)
    assert fee.amount == 100.125 and fee.auction == "Copart"
