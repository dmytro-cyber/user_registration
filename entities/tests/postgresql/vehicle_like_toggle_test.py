from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from crud.vehicle_likes import get_like_vehicle, get_user_with_likes, toggle_user_vehicle_like
from exceptions.vehicle_likes import LikedVehicleNotFoundError
from models.user import UserModel, UserRoleEnum, UserRoleModel, user_likes
from models.vehicle import CarModel
from services import vehicle_likes

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def seed(sessions):
    async with sessions() as db:
        role = UserRoleModel(name=UserRoleEnum.USER)
        db.add(role)
        await db.flush()
        users = [UserModel(email=f"like-{i}@example.com", _hashed_password="unused", role_id=role.id) for i in range(2)]
        car = CarModel(vin="like-car", vehicle="car")
        db.add_all(users + [car])
        await db.commit()
        return car.id, users[0].id, users[1].id


async def rows(sessions):
    async with sessions() as db:
        return list((await db.execute(select(user_likes))).all())


async def test_like_unlike_commits_only_current_user_link(postgres_sessions):
    car, user, other = await seed(postgres_sessions)
    for user_id in [user, other]:
        async with postgres_sessions() as db:
            assert await vehicle_likes.toggle_vehicle_like(db, car_id=car, user_id=user_id) is True
    assert set(await rows(postgres_sessions)) == {(user, car), (other, car)}
    async with postgres_sessions() as db:
        assert await vehicle_likes.toggle_vehicle_like(db, car_id=car, user_id=user) is False
    assert await rows(postgres_sessions) == [(other, car)]
    async with postgres_sessions() as db:
        assert await db.get(CarModel, car) is not None


@pytest.mark.parametrize("initially_liked", [False, True])
async def test_commit_failure_rolls_back_addition_or_removal(postgres_sessions, initially_liked):
    car, user, _ = await seed(postgres_sessions)
    if initially_liked:
        async with postgres_sessions() as db:
            await vehicle_likes.toggle_vehicle_like(db, car_id=car, user_id=user)
    before = await rows(postgres_sessions)
    async with postgres_sessions() as db:
        async def fail_commit():
            await db.flush()
            raise RuntimeError("commit failed")
        db.commit = AsyncMock(side_effect=fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            await vehicle_likes.toggle_vehicle_like(db, car_id=car, user_id=user)
        assert not db.in_transaction()
    assert await rows(postgres_sessions) == before


async def test_missing_vehicle_precedes_user_lookup_without_commit(postgres_sessions):
    async with postgres_sessions() as db:
        db.commit = AsyncMock(side_effect=AssertionError("unexpected commit"))
        with pytest.raises(LikedVehicleNotFoundError):
            await vehicle_likes.toggle_vehicle_like(db, car_id=999999, user_id=999999)


async def test_missing_user_keeps_query_error_and_rolls_back(postgres_sessions):
    car, _, _ = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        with pytest.raises(NoResultFound):
            await vehicle_likes.toggle_vehicle_like(db, car_id=car, user_id=999999)
        assert not db.in_transaction()
    assert await rows(postgres_sessions) == []


async def test_crud_does_not_commit(postgres_sessions):
    car, user, _ = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        vehicle = await get_like_vehicle(db, car)
        account = await get_user_with_likes(db, user)
        assert toggle_user_vehicle_like(account, vehicle) is True
        await db.flush()
        assert await rows(postgres_sessions) == []
        await db.rollback()
    assert await rows(postgres_sessions) == []
