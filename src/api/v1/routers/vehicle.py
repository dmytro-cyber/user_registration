from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import Integer, String
from sqlalchemy.sql import text
from sqlalchemy.orm.attributes import InstrumentedAttribute

from db.session import get_db
from models.vehicle import CarModel
from schemas.vehicle import CarBaseSchema, CarListResponseSchema

from models.vehicle import CarModel
from schemas.vehicle import CarListResponseSchema, CarBaseSchema

router = APIRouter()


from typing import Optional, Dict, Any
from fastapi import Query, Depends
from sqlalchemy.orm.attributes import InstrumentedAttribute


def get_filters(
    mileage_min: Optional[int] = Query(None, description="Min mileage"),
    mileage_max: Optional[int] = Query(None, description="Max miliage"),
    vin: Optional[str] = Query(None, description="VIN-code of the car"),
    vehicle: Optional[str] = Query(None, description="Make model and year"),
    year: Optional[int] = Query(None, description="Year"),
    auction: Optional[str] = Query(None, description="Auction (CoPart/IAAI)"),
    auction_name: Optional[str] = Query(None, description="Auction name"),
    location: Optional[str] = Query(None, description="Location"),
    accident_count: Optional[int] = Query(None, description="Accident count"),
    price_sold: Optional[float] = Query(None, description="Price Sold"),
    recommendation_status: Optional[str] = Query(None, description="Recommendation status (recommended/not_recommended)"),
    car_status: Optional[str] = Query(None, description="Inner status"),
) -> Dict[str, Any]:
    return {
        "mileage_min": mileage_min,
        "mileage_max": mileage_max,
        "vin": vin,
        "vehicle": vehicle,
        "year": year,
        "auction": auction,
        "auction_name": auction_name,
        "location": location,
        "accident_count": accident_count,
        "price_sold": price_sold,
        "recommendation_status": recommendation_status,
        "car_status": car_status,
    }


@router.get("/vehicles/", response_model=CarListResponseSchema)
async def get_cars(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    filters: Dict[str, Any] = Depends(get_filters),
    db: AsyncSession = Depends(get_db),
) -> CarListResponseSchema:
    query = select(CarModel).options(selectinload(CarModel.photos))

    for field, value in filters.items():
        if value is not None and hasattr(CarModel, field):
            column_attr: InstrumentedAttribute = getattr(CarModel, field)
            column_type = column_attr.property.columns[0].type

            if isinstance(column_type, String):
                query = query.filter(column_attr.ilike(f"%{value}%"))
            elif isinstance(column_type, Integer):
                try:
                    query = query.filter(column_attr == int(value))
                except ValueError:
                    pass
            else:
                query = query.filter(column_attr == value)

    total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
    total_pages = (total_count + page_size - 1) // page_size

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    cars = result.scalars().all()

    base_url = str(request.url.remove_query_params("page"))
    page_links = {i: f"{base_url}&page={i}" for i in range(1, total_pages + 1) if i != page}

    return CarListResponseSchema(cars=[CarBaseSchema.model_validate(car) for car in cars], page_links=page_links)
