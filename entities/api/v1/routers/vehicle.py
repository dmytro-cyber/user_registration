import asyncio
import logging
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.vehicle import (
    CarBaseSchema,
    CarListResponseSchema,
    CarDetailResponseSchema,
    UpdateCarStatusSchema,
    PartRequestScheme,
    PartResponseScheme,
    CarCreateSchema,
)
from core.config import Settings
from core.dependencies import get_settings, get_token
from db.session import get_db
from crud.vehicle import (
    get_vehicle_by_vin,
    get_filtered_vehicles,
    get_vehicle_by_id,
    update_vehicle_status,
    add_part_to_vehicle,
    update_part,
    delete_part,
    bulk_save_vehicles,
)
from services.vehicle import (
    scrape_and_save_vehicle,
    prepare_response,
    prepare_car_detail_response,
    scrape_and_save_sales_history,
    car_to_dict,
)
from tasks.task import parse_and_update_car
from typing import List, Optional, Dict

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

router = APIRouter(prefix="/vehicles")


@router.get("/", response_model=CarListResponseSchema)
async def get_cars(
    request: Request,
    auction: List[str] = Query(None, description="Auction (CoPart/IAAI)"),
    auction_name: List[str] = Query(None, description="Auction name"),
    mileage_min: Optional[int] = Query(None, description="Min mileage"),
    mileage_max: Optional[int] = Query(None, description="Max mileage"),
    min_accident_count: Optional[int] = Query(None, description="Min accident count"),
    max_accident_count: Optional[int] = Query(None, description="Max accident count"),
    min_year: Optional[int] = Query(None, description="Min year"),
    max_year: Optional[int] = Query(None, description="Max year"),
    make: List[str] = Query(None, description="Make"),
    model: List[str] = Query(None, description="Model"),
    vin: Optional[str] = Query(None, description="VIN-code of the car"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CarListResponseSchema:
    filters = {
        "auction": auction,
        "auction_name": auction_name,
        "mileage_min": mileage_min,
        "mileage_max": mileage_max,
        "min_accident_count": min_accident_count,
        "max_accident_count": max_accident_count,
        "min_year": min_year,
        "max_year": max_year,
        "make": make,
        "model": model,
    }
    logger.info(f"Fetching cars with filters: {filters}, page: {page}, page_size: {page_size}")

    if vin and len(vin.replace(" ", "")) == 17:
        vin = vin.replace(" ", "")
        logger.info(f"Searching for vehicle with VIN: {vin}")
        async with db.begin():
            vehicle = await get_vehicle_by_vin(db, vin)
            if vehicle:
                logger.info(f"Found vehicle with VIN: {vin}")
                vehicle_data = car_to_dict(vehicle)
                validated_vehicle = CarBaseSchema.model_validate(vehicle_data)
                return CarListResponseSchema(cars=[validated_vehicle], page_links={}, last=True)
            else:
                logger.info(f"Vehicle with VIN {vin} not found in DB, attempting to scrape")
                validated_vehicle = await scrape_and_save_vehicle(vin, db, settings)
                logger.info(f"Scraped and saved data for VIN {vin}, returning response")
                await db.commit()
                return CarListResponseSchema(cars=[validated_vehicle], page_links={}, last=True)

    vehicles, total_count, total_pages = await get_filtered_vehicles(db, filters, page, page_size)
    base_url = str(request.url.remove_query_params("page"))
    response = await prepare_response(vehicles, total_pages, page, base_url)
    logger.info(f"Returning {len(response.cars)} cars, total pages: {total_pages}")
    return response


@router.get("/{car_id}/", response_model=CarDetailResponseSchema)
async def get_car_detail(
    car_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CarDetailResponseSchema:
    logger.info(f"Fetching details for car with ID: {car_id}")

    car = await get_vehicle_by_id(db, car_id)
    if not car:
        logger.warning(f"Car with ID {car_id} not found")
        raise HTTPException(status_code=404, detail="Car not found")

    # if not car.sales_history:
    #     car = await scrape_and_save_sales_history(car, db, settings)

    logger.info(f"Returning details for car with ID: {car_id}")
    return await prepare_car_detail_response(car)


@router.put("/{car_id}/status/", response_model=UpdateCarStatusSchema)
async def update_car_status(
    car_id: int,
    status_data: UpdateCarStatusSchema,
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"Updating status for car with ID: {car_id}, new status: {status_data.car_status}")

    car = await update_vehicle_status(db, car_id, status_data.car_status)
    if not car:
        logger.warning(f"Car with ID {car_id} not found")
        raise HTTPException(status_code=404, detail="Car not found")

    logger.info(f"Status updated for car with ID: {car_id}")
    return status_data


@router.post("/{vehicle_id}/parts/", response_model=PartResponseScheme)
async def add_part(
    vehicle_id: int,
    part: PartRequestScheme,
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"Adding part for car with ID: {vehicle_id}, part: {part.dict()}")

    new_part = await add_part_to_vehicle(db, vehicle_id, part.dict())
    if not new_part:
        logger.warning(f"Car with ID {vehicle_id} not found")
        raise HTTPException(status_code=404, detail="Car not found")

    logger.info(f"Part added for car with ID: {vehicle_id}, part ID: {new_part.id}")
    return new_part


@router.put("/{vehicle_id}/parts/{part_id}/", response_model=PartResponseScheme)
async def update_part(
    vehicle_id: int,
    part_id: int,
    part: PartRequestScheme,
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"Updating part with ID: {part_id} for car with ID: {vehicle_id}")

    updated_part = await update_part(db, vehicle_id, part_id, part.dict())
    if not updated_part:
        logger.warning(f"Part with ID {part_id} for car with ID {vehicle_id} not found")
        raise HTTPException(status_code=404, detail="Part not found")

    logger.info(f"Part with ID: {part_id} updated for car with ID: {vehicle_id}")
    return updated_part


@router.delete("/{vehicle_id}/parts/{part_id}/", status_code=204)
async def delete_part(
    vehicle_id: int,
    part_id: int,
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"Deleting part with ID: {part_id} for car with ID: {vehicle_id}")

    success = await delete_part(db, vehicle_id, part_id)
    if not success:
        logger.warning(f"Part with ID {part_id} for car with ID {vehicle_id} not found")
        raise HTTPException(status_code=404, detail="Part not found")

    logger.info(f"Part with ID: {part_id} deleted for car with ID: {vehicle_id}")
    return {"message": "Part deleted successfully"}


@router.post("/bulk/", status_code=201)
async def bulk_create_cars(
    vehicles: List[CarCreateSchema],
    db: AsyncSession = Depends(get_db),
    token: str = Depends(get_token),
) -> Dict:
    logger.info(f"Starting bulk creation of {len(vehicles)} vehicles")

    skipped_vins = await bulk_save_vehicles(db, vehicles)
    response = {"message": "Cars created successfully"}
    if skipped_vins:
        response["skipped_vins"] = skipped_vins
        logger.info(f"Bulk creation completed, skipped {len(skipped_vins)} vehicles with VINs: {skipped_vins}")
    else:
        logger.info("Bulk creation completed with no skipped vehicles")

    # for vehicle_data in vehicles:
    #     if vehicle_data.vin not in skipped_vins:
    #         parse_and_update_car.delay(vehicle_data.vin, vehicle_data.vehicle, vehicle_data.engine)

    return response
