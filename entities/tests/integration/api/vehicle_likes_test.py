from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from api.v1.routers import vehicle

pytestmark = pytest.mark.anyio


async def test_like_toggle_response_cycle(client, create_car, use_test_user):
    car = await create_car()
    for expected in ["Liked", "Unliked", "Liked"]:
        response = await client.post(f"/api/v1/vehicles/cars/{car.id}/like-toggle")
        assert response.status_code == 200 and response.json() == {"detail": expected}


async def test_like_missing_car_keeps_exact_404(client, use_test_user):
    response = await client.post("/api/v1/vehicles/cars/999999/like-toggle")
    assert response.status_code == 404 and response.json() == {"detail": "Car not found"}


async def test_like_integrity_error_reaches_global_409(client, use_test_user, monkeypatch):
    monkeypatch.setattr(vehicle, "toggle_vehicle_like", AsyncMock(side_effect=IntegrityError("INSERT", {}, RuntimeError())))
    response = await client.post("/api/v1/vehicles/cars/1/like-toggle")
    assert response.status_code == 409 and response.json() == {"detail": "Database integrity error"}
