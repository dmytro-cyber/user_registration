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


@router.get("/vehicles/", response_model=CarListResponseSchema)
async def get_cars(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    mileage_min: int = Query(None),
    mileage_max: int = Query(None),
) -> CarListResponseSchema:
    query = select(CarModel).options(selectinload(CarModel.photos))
    
    filters = request.query_params
    print("FILTERS: ", filters)

    if filters:
        for field, value in filters.items():
            if hasattr(CarModel, field) and value is not None:
                column_attr: InstrumentedAttribute = getattr(CarModel, field)

                column_type = column_attr.property.columns[0].type
                
                if isinstance(column_type, String):
                    query = query.filter(column_attr.ilike(f"%{value}%"))
                elif isinstance(column_type, Integer):
                    try:
                        query = query.filter(column_attr == int(value))
                    except ValueError:
                        print(f"Помилка: Неможливо привести {field} до числа")
                else:
                    query = query.filter(column_attr == value)
    
    if mileage_min is not None:
        query = query.filter(CarModel.mileage >= mileage_min)
    if mileage_max is not None:
        query = query.filter(CarModel.mileage <= mileage_max)
    
    total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
    total_pages = (total_count + page_size - 1) // page_size
    
    result = await db.execute(
        query.offset((page - 1) * page_size).limit(page_size)
    )
    cars = result.scalars().all()
    
    base_url = str(request.url.remove_query_params("page"))
    page_links = {
        i: f"{base_url}&page={i}"
        for i in range(1, total_pages + 1) if i != page 
    }
    
    return CarListResponseSchema(
        cars=[CarBaseSchema.model_validate(car) for car in cars],
        page_links=page_links
    )
