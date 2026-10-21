from unittest.mock import AsyncMock

import pytest

from api.v1.routers import vehicle
from exceptions.vehicle_costs import VehicleCostsPersistenceError

pytestmark = pytest.mark.anyio


async def test_costs_remains_public_and_preserves_null_zero_negative(client, create_car, db_session):
    car = await create_car()
    car.predicted_total_investments = 1000
    car.parts_cost = 10
    car.auction_fee = 20
    await db_session.commit()
    response = await client.put(f"/api/v1/vehicles/cars/{car.id}/costs",
                                json={"maintenance": None, "transportation": -10, "labor": 0})
    assert response.status_code == 200, response.text
    assert response.json() == {"id": car.id, "maintenance": None, "transportation": -10,
                               "labor": 0, "suggested_bid": 980}


async def test_costs_empty_and_missing_errors(client, create_car):
    car = await create_car()
    response = await client.put(f"/api/v1/vehicles/cars/{car.id}/costs", json={})
    assert response.status_code == 400 and response.json() == {"detail": "No valid fields provided for update"}
    response = await client.put("/api/v1/vehicles/cars/999999/costs", json={})
    assert response.status_code == 404 and response.json() == {"detail": "Car with ID 999999 not found"}


async def test_costs_persistence_failure_keeps_500(client, monkeypatch):
    monkeypatch.setattr(vehicle, "update_vehicle_costs", AsyncMock(side_effect=VehicleCostsPersistenceError()))
    response = await client.put("/api/v1/vehicles/cars/1/costs", json={"maintenance": 1})
    assert response.status_code == 500 and response.json() == {"detail": "Internal server error"}
