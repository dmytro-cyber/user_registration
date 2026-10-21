"""Application transaction for toggling a user's vehicle like."""

from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle_likes import get_like_vehicle, get_user_with_likes, toggle_user_vehicle_like
from exceptions.vehicle_likes import LikedVehicleNotFoundError


async def toggle_vehicle_like(db: AsyncSession, *, car_id: int, user_id: int) -> bool:
    try:
        car = await get_like_vehicle(db, car_id)
        if car is None:
            raise LikedVehicleNotFoundError()
        user = await get_user_with_likes(db, user_id)
        liked = toggle_user_vehicle_like(user, car)
        await db.commit()
        return liked
    except LikedVehicleNotFoundError:
        raise
    except Exception:
        await db.rollback()
        raise
