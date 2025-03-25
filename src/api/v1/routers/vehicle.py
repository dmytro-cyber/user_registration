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
            vehicle=car.vehicle,
            year=car.year,
            mileage=car.mileage,
            auction=car.auction,
            auction_name=car.auction_name,
            date=car.date,
            lot=car.lot,
            seller=car.seller,
            owners=car.owners,
            accident_count=car.accident_count,
            engine=car.engine,
            has_keys=car.has_keys,
            predicted_roi=car.predicted_roi,
            predicted_profit_margin=car.predicted_profit_margin,
            bid=car.bid,
            suggested_bid=car.suggested_bid,
            photos=car.photos
        )
        for car in cars
    ]
    return result
