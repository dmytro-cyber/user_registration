from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from db.session import get_db
from models.vehicle import Car
from schemas.vehicle import CarBase, CarListResponseSchema

router = APIRouter()


@router.get("/cars", response_model=CarListResponseSchema)
async def get_cars(db: AsyncSession = Depends(get_db)) -> CarListResponseSchema:
    result = await db.execute(select(Car))
    cars = result.scalars().all()
    result = [
        CarBase(
            vin=car.vin,
            vehicle=car.vehicle or None,
            year=car.year or None,
            mileage=car.mileage or None,
            auction=car.auction or None,
            auction_name=car.auction_name or None,
            date=car.date or None,
            lot=car.lot or None,
            seller=car.seller or None,
            owners=car.owners or None,
            accident_count=car.accident_count or None,
            engine=car.engine or None,
            has_keys=car.has_keys or None,
            predicted_roi=car.predicted_roi or None,
            predicted_profit_margin=car.predicted_profit_margin or None,
            bid=car.bid or None,
            suggested_bid=car.suggested_bid or None,
            photos=car.photos or [],
        )
        for car in cars
    ]
    return result
