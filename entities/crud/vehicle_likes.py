"""Persistence for user vehicle likes; callers own transactions."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.user import UserModel
from models.vehicle import CarModel


async def get_like_vehicle(db: AsyncSession, car_id: int) -> CarModel | None:
    result = await db.execute(select(CarModel).where(CarModel.id == car_id))
    return result.scalar_one_or_none()


async def get_user_with_likes(db: AsyncSession, user_id: int) -> UserModel:
    result = await db.execute(
        select(UserModel).options(selectinload(UserModel.liked_cars)).where(UserModel.id == user_id)
    )
    return result.scalar_one()


def toggle_user_vehicle_like(user: UserModel, car: CarModel) -> bool:
    if car in user.liked_cars:
        user.liked_cars.remove(car)
        return False
    user.liked_cars.append(car)
    return True
