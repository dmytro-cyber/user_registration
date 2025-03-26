from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from db.session import get_db
from models.vehicle import CarModel
from schemas.vehicle import CarBaseSchema, CarListResponseSchema

from models.vehicle import CarModel
from schemas.vehicle import CarListResponseSchema, CarBaseSchema

router = APIRouter()


@router.get("/vehicles/", response_model=CarListResponseSchema)
async def get_cars(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> CarListResponseSchema:
    total_count = await db.scalar(select(func.count()).select_from(CarModel))
    total_pages = (total_count + page_size - 1) // page_size

    result = await db.execute(
        select(CarModel).options(selectinload(CarModel.photos)).offset((page - 1) * page_size).limit(page_size)
    )
    cars = result.scalars().all()

    base_url = str(request.url)
    next_page_url = f"{base_url.split('?')[0]}?page={page + 1}&page_size={page_size}" if page < total_pages else None
    prev_page_url = f"{base_url.split('?')[0]}?page={page - 1}&page_size={page_size}" if page > 1 else None

    return CarListResponseSchema(
        cars=[CarBaseSchema.model_validate(car) for car in cars],
        total_pages=total_pages,
        next_page=next_page_url,
        prev_page=prev_page_url,
    )
