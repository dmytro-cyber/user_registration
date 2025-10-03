from logging import getLogger
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from crud.vehicle import (
    get_vehicle_by_id,
    get_vehicle_by_vin,
    save_sale_history,
    save_vehicle,
    save_vehicle_with_photos,
)
from models.vehicle import CarModel
from schemas.vehicle import (
    CarBaseSchema,
    CarCreateSchema,
    CarDetailResponseSchema,
    CarListResponseSchema,
    ConditionAssessmentResponseSchema,
    PartResponseScheme,
    SalesHistoryBaseSchema,
)

logger = getLogger(__name__)


def car_to_dict(vehicle: "CarModel") -> dict:
    """Convert a CarModel to a plain dict without touching SQLAlchemy internals."""
    return {
        "id": vehicle.id,
        "vin": vehicle.vin,
        "vehicle": vehicle.vehicle,
        "year": vehicle.year,
        "mileage": vehicle.mileage,
        "auction": vehicle.auction,
        "auction_name": vehicle.auction_name,
        "date": vehicle.date,
        "lot": vehicle.lot,
        "seller": vehicle.seller,
        "owners": vehicle.owners,
        "accident_count": vehicle.accident_count,
        "engine": vehicle.engine,
        "has_keys": vehicle.has_keys,
        "predicted_roi": vehicle.predicted_roi,
        "predicted_profit_margin": vehicle.predicted_profit_margin,
        "roi": vehicle.roi,
        "profit_margin": vehicle.profit_margin,
        "current_bid": vehicle.current_bid,
        "suggested_bid": vehicle.suggested_bid,
        "location": vehicle.location,
        "has_correct_mileage": vehicle.has_correct_mileage,
        "has_correct_vin": vehicle.has_correct_vin,
        "has_correct_accidents": vehicle.has_correct_accidents,
        "liked": bool(getattr(vehicle, "liked", False)),
        "recommendation_status": vehicle.recommendation_status,
        "recommendation_status_reasons": vehicle.recommendation_status_reasons,
        "photos": [p.url for p in (vehicle.photos or [])],
    }


async def scrape_and_save_vehicle(vin: str, db: AsyncSession, settings: Settings) -> CarBaseSchema:
    """Scrape vehicle data by VIN and save it to the database."""
    httpx_client = httpx.AsyncClient(timeout=10.0)
    httpx_client.headers.update({"X-Auth-Token": settings.PARSERS_AUTH_TOKEN})
    try:
        response = await httpx_client.get(f"http://parsers:8001/api/v1/apicar/get/{vin}")
        response.raise_for_status()
        result = CarCreateSchema.model_validate(response.json())
    except httpx.HTTPError as e:
        logger.warning(f"Failed to scrape data for VIN {vin}: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Failed to fetch data from parser: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to validate scraped data for VIN {vin}: {str(e)}")
        raise HTTPException(status_code=422, detail=f"Invalid data from parser: {str(e)}")

    saved = await save_vehicle_with_photos(result, db)
    # await db.commit()
    if not saved:
        logger.warning(f"Vehicle with VIN {vin} already exists in DB")
        raise HTTPException(status_code=409, detail=f"Vehicle with VIN {vin} already exists")

    vehicle = await get_vehicle_by_vin(db, vin, 1)
    if not vehicle:
        logger.error(f"Failed to retrieve saved vehicle with VIN {vin}")
        raise HTTPException(status_code=500, detail="Failed to retrieve saved vehicle")

    vehicle_data = car_to_dict(vehicle)
    return CarBaseSchema.model_validate(vehicle_data)


async def prepare_response(
    vehicles: List[CarModel], total_pages: int, page: int, base_url: str, bid_info: dict
) -> CarListResponseSchema:
    """Prepare the response with validated cars and pagination links."""
    validated_cars = []
    for car in vehicles:
        try:
            car_data = car_to_dict(car)
            car_data["liked"] = car.liked
            validated_car = CarBaseSchema.model_validate(car_data)
            validated_cars.append(validated_car)
        except Exception as e:
            logger.error(f"Failed to validate car VIN {car.vin}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Validation error for car VIN {car.vin}: {str(e)}")

    page_links = {i: f"{base_url}&page={i}" for i in range(1, total_pages + 1) if i != page}
    return CarListResponseSchema(
        cars=validated_cars, page_links=page_links, last=(total_pages == page), bid_info=bid_info
    )


async def prepare_car_detail_response(car: CarModel) -> CarDetailResponseSchema:
    """Prepare the detailed response for a car."""
    return CarDetailResponseSchema(
        id=car.id,
        auction=car.auction,
        vehicle=car.vehicle,
        vin=car.vin,
        mileage=car.mileage,
        has_keys=car.has_keys,
        engine_and_cylinder=car.engine_and_cylinder,
        drive_type=car.drive_type,
        transmision=car.transmision,
        vehicle_type=car.vehicle_type,
        exterior_color=car.exterior_color,
        body_style=car.body_style,
        interior_color=car.interior_color,
        style_id=car.style_id,
        seller=car.seller,
        lot=car.lot,
        actual_bid=car.actual_bid,
        owners=car.owners,
        accident_count=car.accident_count,
        date=car.date,
        recommendation_status=car.recommendation_status.value,
        link=car.link,
        location=car.location,
        auction_fee=car.auction_fee,
        suggested_bid=car.suggested_bid,
        auction_name=car.auction_name,
        liked=car.liked,
        has_correct_mileage=car.has_correct_mileage,
        has_correct_vin=car.has_correct_vin,
        has_correct_accidents=car.has_correct_accidents,
        labor=car.labor,
        transportation=car.transportation,
        maintenance=car.maintenance,
        condition=car.condition,
        photos=[photo.url for photo in car.photos_hd] if car.photos_hd else [],
        condition_assessments=[
            ConditionAssessmentResponseSchema(
                type_of_damage=condition.type_of_damage, issue_description=condition.issue_description
            )
            for condition in car.condition_assessments
        ],
        sales_history=(
            [SalesHistoryBaseSchema(**sales_history.__dict__) for sales_history in car.sales_history]
            if car.sales_history
            else []
        ),
        additional_info={
            "avg_price": car.avg_market_price or None,
            "predicted_total_investment": car.predicted_total_investments or None,
            "predicted_profit_margin": car.predicted_profit_margin or None,
            "predicted_roi": car.predicted_roi or None,
            "current_bid": car.current_bid,
        },
    )


async def scrape_and_save_sales_history(vin: str, db: AsyncSession, settings: Settings) -> CarModel:
    """Scrape sales history for a car and save it."""
    async with httpx.AsyncClient(timeout=10.0, headers={"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}) as httpx_client:
        try:
            response = await httpx_client.get(f"http://parsers:8001/api/v1/apicar/get/{vin}")
            response.raise_for_status()
            result = CarCreateSchema.model_validate(response.json())
            logger.info(f"Successfully scraped sales history data {result.sales_history}")
            car = await get_vehicle_by_vin(db, vin, 1)  # Ensure the vehicle exists before saving sales history
            await save_sale_history(result.sales_history, car.id, db)

            return car
        except httpx.HTTPError as e:
            logger.warning(f"Failed to scrape data for VIN {vin}: {str(e)}")
            raise HTTPException(status_code=503, detail=f"Failed to fetch data from parser: {str(e)}")


def build_car_filter_query(filter_obj):
    conditions = []

    if filter_obj.make:
        conditions.append(CarModel.make == filter_obj.make)
    if filter_obj.model:
        conditions.append(CarModel.model == filter_obj.model)
    if filter_obj.year_from:
        conditions.append(CarModel.year >= filter_obj.year_from)
    if filter_obj.year_to:
        conditions.append(CarModel.year <= filter_obj.year_to)
    if filter_obj.odometer_min:
        conditions.append(CarModel.mileage >= filter_obj.odometer_min)
    if filter_obj.odometer_max:
        conditions.append(CarModel.mileage <= filter_obj.odometer_max)

    return conditions