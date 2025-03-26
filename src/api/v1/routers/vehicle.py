from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from sqlalchemy.orm import selectinload

from db.session import get_db
from models.vehicle import Car, Photo
from schemas.vehicle import CarBase, CarListResponseSchema

router = APIRouter()


@router.get("/vehicles/", response_model=CarListResponseSchema)
async def get_cars(db: AsyncSession = Depends(get_db)) -> CarListResponseSchema:
    result = await db.execute(select(Car).options(selectinload(Car.photos)))
    cars = result.scalars().all()
    result = [
        CarBase.model_validate(car)
        for car in cars
    ]
    return CarListResponseSchema(cars=result)
