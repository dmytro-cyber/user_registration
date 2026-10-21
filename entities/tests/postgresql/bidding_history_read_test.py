from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest

from crud.bidding_hub import get_vehicle_history
from exceptions.bidding import BiddingHistoryNotFoundError
from models.user import UserModel, UserRoleEnum, UserRoleModel
from models.vehicle import CarModel, HistoryModel
from services.bidding_hub import get_vehicle_bidding_history

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_history_eager_relationships_and_serialization_without_transaction_writes(postgres_sessions):
    async with postgres_sessions() as db:
        role = UserRoleModel(name=UserRoleEnum.USER)
        db.add(role)
        await db.flush()
        user = UserModel(email="history@example.com", _hashed_password="unused", role_id=role.id,
                         first_name="First", last_name="Last", phone_number="123", date_of_birth=date(1990, 1, 1))
        car = CarModel(vin="history-read", vehicle="car")
        db.add_all([user, car])
        await db.flush()
        car_id = car.id
        db.add_all([
            HistoryModel(car_id=car_id, user_id=user.id, action="old", created_at=datetime(2020, 1, 1)),
            HistoryModel(car_id=car_id, action="new", created_at=datetime(2021, 1, 1)),
        ])
        await db.commit()
    async with postgres_sessions() as db:
        db.commit = AsyncMock(side_effect=AssertionError("read committed"))
        db.rollback = AsyncMock(side_effect=AssertionError("read rolled back"))
        rows = await get_vehicle_history(db, car_id)
        response = await get_vehicle_bidding_history(db, car_id=car_id)
    assert [row.action for row in rows] == ["new", "old"]
    assert rows[1].user.role.name == UserRoleEnum.USER
    assert response.model_dump(mode="json")["history"][1]["user"] == {
        "email": "history@example.com", "first_name": "First", "last_name": "Last",
        "phone_number": "123", "date_of_birth": "1990-01-01", "role": "user",
    }
    assert response.history[0].user is None


async def test_missing_history_raises_without_commit_or_rollback(postgres_sessions):
    async with postgres_sessions() as db:
        db.commit = AsyncMock(side_effect=AssertionError("read committed"))
        db.rollback = AsyncMock(side_effect=AssertionError("read rolled back"))
        with pytest.raises(BiddingHistoryNotFoundError):
            await get_vehicle_bidding_history(db, car_id=777777)
