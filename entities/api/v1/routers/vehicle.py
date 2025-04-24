import asyncio
import logging
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import Integer, String
from sqlalchemy.orm.attributes import InstrumentedAttribute
import httpx

from db.session import get_db
from models.vehicle import CarModel, PartModel
from schemas.vehicle import (
    CarBaseSchema,
    CarListResponseSchema,
    CarDetailResponseSchema,
    UpdateCarStatusSchema,
    PartRequestScheme,
    PartResponseScheme,
    CarCreateSchema,
    SalesHistoryBaseSchema
)
from core.config import Settings
from core.dependencies import get_settings, get_token
from crud.vehicle import save_vehicle, save_sale_history
from tasks.task import parse_and_update_car
from typing import Optional, Dict, Any, List

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

router = APIRouter()


def car_to_dict(vehicle: CarModel) -> Dict[str, Any]:
    photos_data = [photo.url for photo in vehicle.photos] if vehicle.photos else []

    return {**vehicle.__dict__, "photos": photos_data}


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
    logger.info(f"Fetching cars with filters: {filters}, page: {page}, page_size: {page_size}")
    
    query = select(CarModel).options(selectinload(CarModel.photos))

    if filters.get("vin") and len(filters.get("vin").replace(" ", "")) == 17:
        vin = filters.get("vin").replace(" ", "")
        logger.info(f"Searching for vehicle with VIN: {vin}")
        async with db.begin():
            vehicle_result = await db.execute(
                select(CarModel).options(selectinload(CarModel.photos)).filter(CarModel.vin == vin)
            )
            vehicle = vehicle_result.scalars().first()
            if vehicle:
                logger.info(f"Found vehicle with VIN: {vin}")
                vehicle_data = car_to_dict(vehicle)
                validated_vehicle = CarBaseSchema.model_validate(vehicle_data)
                return CarListResponseSchema(
                    cars=[validated_vehicle],
                    page_links={},
                )
            else:
                logger.info(f"Vehicle with VIN {vin} not found in DB, attempting to scrape")
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

                saved = await save_vehicle(result, db)
                await db.commit()
                if not saved:
                    logger.warning(f"Vehicle with VIN {vin} already exists in DB")
                    raise HTTPException(status_code=409, detail=f"Vehicle with VIN {vin} already exists")

                vehicle_result = await db.execute(
                    select(CarModel).options(selectinload(CarModel.photos)).filter(CarModel.vin == vin)
                )
                vehicle = vehicle_result.scalars().first()
                if not vehicle:
                    logger.error(f"Failed to retrieve saved vehicle with VIN {vin}")
                    raise HTTPException(status_code=500, detail="Failed to retrieve saved vehicle")

                vehicle_data = car_to_dict(vehicle)
                validated_vehicle = CarBaseSchema.model_validate(vehicle_data)
                
                logger.info(f"Scraped and saved data for VIN {vin}, returning response")
                return CarListResponseSchema(cars=[validated_vehicle], page_links={})

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

    for car in cars:
        logger.debug(f"Car VIN: {car.vin}, photos: {car.photos}")

    validated_cars = []
    for car in cars:
        try:
            car_data = car_to_dict(car)
            validated_car = CarBaseSchema.model_validate(car_data)
            validated_cars.append(validated_car)
        except Exception as e:
            logger.error(f"Failed to validate car VIN {car.vin}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Validation error for car VIN {car.vin}: {str(e)}")

    base_url = str(request.url.remove_query_params("page"))
    page_links = {i: f"{base_url}&page={i}" for i in range(1, total_pages + 1) if i != page}

    logger.info(f"Returning {len(validated_cars)} cars, total pages: {total_pages}")
    return CarListResponseSchema(cars=validated_cars, page_links=page_links)


@router.get("/vehicles/{car_id}/", response_model=CarDetailResponseSchema)
async def get_car_detail(car_id: int, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings),) -> CarDetailResponseSchema:
    logger.info(f"Fetching details for car with ID: {car_id}")

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
        logger.warning(f"Car with ID {car_id} not found")
        raise HTTPException(status_code=404, detail="Car not found")
    
    # if not car.sales_history:
    #     httpx_client = httpx.AsyncClient(timeout=10.0)
    #     httpx_client.headers.update({"X-Auth-Token": settings.PARSERS_AUTH_TOKEN})
    #     try:
    #         response = await httpx_client.get(f"http://parsers:8001/api/v1/apicar/get/{car.vin}")
    #         response.raise_for_status()
    #         result = CarCreateSchema.model_validate(response.json())
    #         save_sale_history(result.sales_history, car.id, db)

    #         result = await db.execute(
    #             select(CarModel)
    #             .options(
    #                 selectinload(CarModel.photos),
    #                 selectinload(CarModel.condition_assessment),
    #                 selectinload(CarModel.sales_history),
    #             )
    #             .filter(CarModel.id == car_id)
    #         )
    #         car = result.scalars().first()
    #     except httpx.HTTPError as e:
    #         logger.warning(f"Failed to scrape data for VIN {car.vin}: {str(e)}")
    #         raise HTTPException(status_code=503, detail=f"Failed to fetch data from parser: {str(e)}")

    logger.info(f"Returning details for car with ID: {car_id}")
    return CarDetailResponseSchema(
        id = car.id,
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
        photos=[photo.url for photo in car.photos] if car.photos else [],
        condition_assessment=[],
        sales_history=[SalesHistoryBaseSchema(**sales_history.__dict__) for sales_history in car.sales_history] if car.sales_history else [],
    )


@router.put("/vehicles/{car_id}/status/", response_model=UpdateCarStatusSchema)
async def update_car_status(car_id: int, status_data: UpdateCarStatusSchema, db: AsyncSession = Depends(get_db)):
    logger.info(f"Updating status for car with ID: {car_id}, new status: {status_data.car_status}")

    async with db.begin():
        result = await db.execute(select(CarModel).where(CarModel.id == car_id))
        car = result.scalars().first()

        if not car:
            logger.warning(f"Car with ID {car_id} not found")
            raise HTTPException(status_code=404, detail="Car not found")

        car.car_status = status_data.car_status
        await db.commit()
        await db.refresh(car)

    logger.info(f"Status updated for car with ID: {car_id}")
    return status_data


@router.post("/vehicles/{vehicle_id}/parts/", response_model=PartResponseScheme)
async def add_part(car_id: int, part: PartRequestScheme, db: AsyncSession = Depends(get_db)):
    logger.info(f"Adding part for car with ID: {car_id}, part: {part.dict()}")

    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        logger.warning(f"Car with ID {car_id} not found")
        raise HTTPException(status_code=404, detail="Car not found")

    new_part = PartModel(**part.dict(), car_id=car_id)
    db.add(new_part)
    await db.commit()
    await db.refresh(new_part)

    logger.info(f"Part added for car with ID: {car_id}, part ID: {new_part.id}")
    return new_part


@router.put("/vehicles/{vehicle_id}/parts/{part_id}/", response_model=PartResponseScheme)
async def update_part(car_id: int, part_id: int, part: PartRequestScheme, db: AsyncSession = Depends(get_db)):
    logger.info(f"Updating part with ID: {part_id} for car with ID: {car_id}")

    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    existing_part = result.scalars().first()
    if not existing_part:
        logger.warning(f"Part with ID {part_id} for car with ID {car_id} not found")
        raise HTTPException(status_code=404, detail="Part not found")

    for key, value in part.dict().items():
        setattr(existing_part, key, value)

    await db.commit()
    await db.refresh(existing_part)

    logger.info(f"Part with ID: {part_id} updated for car with ID: {car_id}")
    return existing_part


@router.delete("/vehicles/{vehicle_id}/parts/{part_id}/", status_code=204)
async def delete_part(car_id: int, part_id: int, db: AsyncSession = Depends(get_db)):
    logger.info(f"Deleting part with ID: {part_id} for car with ID: {car_id}")

    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    part = result.scalars().first()
    if not part:
        logger.warning(f"Part with ID {part_id} for car with ID {car_id} not found")
        raise HTTPException(status_code=404, detail="Part not found")

    await db.delete(part)
    await db.commit()

    logger.info(f"Part with ID: {part_id} deleted for car with ID: {car_id}")
    return {"message": "Part deleted successfully"}


@router.post("/vehicles/bulk/", status_code=201)
async def bulk_create_cars(
    vehicles: List[CarCreateSchema], db: AsyncSession = Depends(get_db), token: str = Depends(get_token)
) -> Dict:
    """
    Bulk create cars, ignoring vehicles with duplicate VINs.

    Args:
        vehicles: List of vehicle data to create.
        db: Database session.
        token: Authentication token.

    Returns:
        Dict with success message and list of skipped VINs (if any).
    """
    logger.info(f"Starting bulk creation of {len(vehicles)} vehicles")

    skipped_vins = []

    for vehicle_data in vehicles:
        success = await save_vehicle(vehicle_data, db)
        await db.commit()
        
        if success:
            logger.info(f"Vehicle with VIN: {vehicle_data.vin} created successfully")
            parse_and_update_car.delay(vehicle_data.vin)
        else:
            logger.warning(f"Skipped vehicle with VIN: {vehicle_data.vin} due to duplicate")
            skipped_vins.append(vehicle_data.vin)
            parse_and_update_car.delay(vehicle_data.vin)

    response = {"message": "Cars created successfully"}
    if skipped_vins:
        response["skipped_vins"] = skipped_vins
        logger.info(f"Bulk creation completed, skipped {len(skipped_vins)} vehicles with VINs: {skipped_vins}")
    else:
        logger.info("Bulk creation completed with no skipped vehicles")

    return response
