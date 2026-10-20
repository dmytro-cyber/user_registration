"""Persistence operations for bidding scenarios; transaction ownership is external."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.vehicle import CarModel, FeeModel


async def get_vehicle_for_bid_update(db: AsyncSession, car_id: int) -> CarModel | None:
    result = await db.execute(select(CarModel).where(CarModel.id == car_id).with_for_update())
    return result.scalars().first()


async def get_bid_fees(db: AsyncSession, auction: str, actual_bid: float) -> list[FeeModel]:
    result = await db.execute(
        select(FeeModel).where(
            FeeModel.auction.ilike(auction),
            FeeModel.price_from <= actual_bid,
            FeeModel.price_to >= actual_bid,
        )
    )
    return list(result.scalars().all())
