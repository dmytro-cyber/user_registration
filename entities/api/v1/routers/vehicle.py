import asyncio
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import Integer, String
from sqlalchemy.sql import text
from sqlalchemy.orm.attributes import InstrumentedAttribute
import httpx

from db.session import get_db
from models.vehicle import CarModel
from schemas.vehicle import (
    CarBaseSchema,
    CarListResponseSchema,
    CarDeteilResponseSchema,
    UpdateCarStatusSchema,
    PartRequestScheme,
    PartResponseScheme,
)
from core.config import Settings
from core.dependencies import get_settings

from typing import Optional, Dict, Any
from fastapi import Query, Depends
from sqlalchemy.orm.attributes import InstrumentedAttribute


router = APIRouter()


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
    recommendation_status: Optional[str] = Query(
        None, description="Recommendation status (recommended/not_recommended)"
    ),
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
    settings: Settings = Depends(get_settings),
) -> CarListResponseSchema:
    query = select(CarModel).options(selectinload(CarModel.photos))

    if filters.get("vin") and len(filters.get("vin").replace(" ", "")) == 17:
        vin = filters.get("vin").replace(" ", "")
        vehicle_result = await db.execute(
            select(CarModel).options(selectinload(CarModel.photos)).filter(CarModel.vin == vin)
        )
        vehicle = vehicle_result.scalars().first()
        if vehicle:
            return CarListResponseSchema(
                cars=[
                    CarBaseSchema.model_validate(vehicle),
                ],
                page_links={},
            )
        else:
            httpx_client = httpx.AsyncClient(timeout=300.0)
            httpx_client.headers.update({"X-Auth-Token": settings.PARSERS_AUTH_TOKEN})
            response = await httpx_client.get(
                f"http://parsers:8001/api/v1/parsers/scrape/dc/{vin}")
            result = response.json()
            if result.get("error"):
                raise HTTPException(status_code=404, detail="Information not found")
                # result = await asyncio.to_thread(scraper.scrape)
            # scraped_car = CarModel(vehicle=result.get("vehicle"), vin=vin)
            # scraped_car.year = result.get("year")
            # scraped_car.mileage = result.get("mileage")
            # scraped_car.owners = result.get("owners")
            # scraped_car.accident_count = result.get("accident_count")
            # db.add(scraped_car)
            # await db.commit()
            # await db.refresh(scraped_car)
            response_scraped_car = CarBaseSchema(
                vin=vin,
                vehicle=result.get("vehicle"),
                year=result.get("year"),
                mileage=result.get("mileage"),
                auction=None,
                auction_name=None,
                date=None,
                lot=None,
                seller=None,
                owners=result.get("owners"),
                accident_count=result.get("accident_count"),
                engine=None,
                has_keys=None,
                predicted_roi=None,
                predicted_profit_margin=None,
                bid=None,
                suggested_bid=None,
                location=None,
                photos=[],
            )
            return CarListResponseSchema(cars=[response_scraped_car], page_links={})

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


@router.get("/vehicles/{car_id}/", response_model=CarDeteilResponseSchema)
async def get_car_detail(car_id: int, db: AsyncSession = Depends(get_db)) -> CarDeteilResponseSchema:
    result = await db.execute(
        select(CarModel)
        .options(
            selectinload(CarModel.photos),
            selectinload(CarModel.condition_assessment),
            selectinload(CarModel.sales_history),
        )
        .filter(CarModel.id == car_id)
    )
    car = result.scalars().first()

    if not car:
        raise HTTPException(status_code=404, detail="Car not found")

    return car


@router.put("/cars/{car_id}/status", response_model=UpdateCarStatusSchema)
async def update_car_status(car_id: int, status_data: UpdateCarStatusSchema, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        result = await db.execute(select(CarModel).where(CarModel.id == car_id))
        car = result.scalars().first()

        if not car:
            raise HTTPException(status_code=404, detail="Car not found")

        car.car_status = status_data.car_status
        await db.commit()
        await db.refresh(car)

    return status_data


@router.post("/cars/{car_id}/parts", response_model=PartResponseScheme)
async def add_part(car_id: int, part: PartRequestScheme, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")

    new_part = PartModel(**part.dict(), car_id=car_id)
    db.add(new_part)
    await db.commit()
    await db.refresh(new_part)
    return new_part


@router.put("/cars/{car_id}/parts/{part_id}", response_model=PartResponseScheme)
async def update_part(car_id: int, part_id: int, part: PartRequestScheme, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    existing_part = result.scalars().first()
    if not existing_part:
        raise HTTPException(status_code=404, detail="Part not found")

    for key, value in part.dict().items():
        setattr(existing_part, key, value)

    await db.commit()
    await db.refresh(existing_part)
    return existing_part


@router.delete("/cars/{car_id}/parts/{part_id}", status_code=204)
async def delete_part(car_id: int, part_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    part = result.scalars().first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")

    await db.delete(part)
    await db.commit()
    return {"message": "Part deleted successfully"}
