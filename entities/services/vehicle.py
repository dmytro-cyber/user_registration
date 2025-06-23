from typing import List, Dict, Any
import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.vehicle import (
    CarListResponseSchema,
    CarBaseSchema,
    CarCreateSchema,
    CarDetailResponseSchema,
    PartResponseScheme,
    SalesHistoryBaseSchema,
    ConditionAssessmentResponseSchema,
)
from models.vehicle import CarModel
from crud.vehicle import (
    get_vehicle_by_vin,
    save_vehicle,
    get_vehicle_by_id,
    save_sale_history,
    save_vehicle_with_photos,
)
from logging import getLogger
from core.config import Settings

logger = getLogger(__name__)


def car_to_dict(vehicle: CarModel) -> Dict[str, Any]:
    """Convert a CarModel to a dictionary."""
    photos_data = [photo.url for photo in vehicle.photos] if vehicle.photos else []
    return {**vehicle.__dict__, "photos": photos_data}


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

    vehicle = await get_vehicle_by_vin(db, vin)
    if not vehicle:
        logger.error(f"Failed to retrieve saved vehicle with VIN {vin}")
        raise HTTPException(status_code=500, detail="Failed to retrieve saved vehicle")

    vehicle_data = car_to_dict(vehicle)
    return CarBaseSchema.model_validate(vehicle_data)


async def prepare_response(
    vehicles: List[CarModel], total_pages: int, page: int, base_url: str
) -> CarListResponseSchema:
    """Prepare the response with validated cars and pagination links."""
    validated_cars = []
    for car in vehicles:
        try:
            # car_data = car_to_dict(car)
            validated_car = CarBaseSchema.model_validate(car)
            validated_cars.append(validated_car)
        except Exception as e:
            logger.error(f"Failed to validate car VIN {car.get("vin", None)}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Validation error for car VIN {car.vin}: {str(e)}")

    page_links = {i: f"{base_url}&page={i}" for i in range(1, total_pages + 1) if i != page}
    return CarListResponseSchema(cars=validated_cars, page_links=page_links, last=(total_pages == page))


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
        lot=car.lot,
        owners=car.owners,
        accident_count=car.accident_count,
        date=car.date,
        link=car.link,
        location=car.location,
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
    )


async def scrape_and_save_sales_history(car: CarModel, db: AsyncSession, settings: Settings) -> CarModel:
    """Scrape sales history for a car and save it."""
    httpx_client = httpx.AsyncClient(timeout=10.0)
    httpx_client.headers.update({"X-Auth-Token": settings.PARSERS_AUTH_TOKEN})
    try:
        response = await httpx_client.get(f"http://parsers:8001/api/v1/apicar/get/{car.vin}")
        response.raise_for_status()
        result = CarCreateSchema.model_validate(response.json())
        logger.info(f"Successfully scraped sales history data {result.sales_history}")
        await save_sale_history(result.sales_history, car.id, db)

        updated_car = await get_vehicle_by_id(db, car.id)
        if not updated_car:
            logger.error(f"Failed to retrieve updated car with ID {car.id}")
            raise HTTPException(status_code=500, detail="Failed to retrieve updated car")
        return updated_car
    except httpx.HTTPError as e:
        logger.warning(f"Failed to scrape data for VIN {car.vin}: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Failed to fetch data from parser: {str(e)}")
