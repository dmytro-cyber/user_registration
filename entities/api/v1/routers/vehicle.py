import asyncio
import logging
import logging.handlers
import os
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import distinct, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.celery_config import app as celery_app
from core.config import Settings
from core.dependencies import get_current_user, get_settings, get_token
from crud.vehicle import (
    add_part_to_vehicle,
    bulk_save_vehicles,
    delete_part,
    get_filtered_vehicles,
    get_parts_by_vehicle_id,
    get_vehicle_by_id,
    get_vehicle_by_vin,
    save_vehicle_with_photos,
    update_part,
    update_vehicle_status,
    update_cars_relevance,
)
from db.session import get_db
from models.user import UserModel
from models.vehicle import AutoCheckModel, CarModel, ConditionAssessmentModel, FeeModel, HistoryModel, RelevanceStatus
from models.admin import ROIModel
from schemas.vehicle import (
    CarBaseSchema,
    CarBulkCreateSchema,
    CarCostsUpdateRequestSchema,
    CarCreateSchema,
    CarDetailResponseSchema,
    CarFilterOptionsSchema,
    CarListResponseSchema,
    CarUpdateSchema,
    PartRequestScheme,
    PartResponseScheme,
    UpdateCarStatusSchema,
)
from services.vehicle import (
    car_to_dict,
    prepare_car_detail_response,
    prepare_response,
    scrape_and_save_sales_history,
    scrape_and_save_vehicle,
)
# from tasks.task import parse_and_update_car

# Configure logging for production environment
logger = logging.getLogger("vehicles_router")
logger.setLevel(logging.DEBUG)  # Set the default logging level

# Define formatter for structured logging
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - [RequestID: %(request_id)s] - [UserID: %(user_id)s] - %(message)s"
)

# Comment out file logging setup to disable writing to file
# log_directory = "logs"
# if not os.path.exists(log_directory):
#     os.makedirs(log_directory)
# file_handler = logging.handlers.RotatingFileHandler(
#     filename="logs/vehicles.log",
#     maxBytes=10 * 1024 * 1024,  # 10 MB
#     backupCount=5,  # Keep up to 5 backup files
# )
# file_handler.setFormatter(formatter)
# file_handler.setLevel(logging.DEBUG)

# Set up console handler for debug output
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# Add handlers to the logger (only console handler is active)
# logger.addHandler(file_handler)  # Comment out to disable file logging
logger.addHandler(console_handler)


# Custom filter to add context (RequestID, UserID)
class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, "request_id", "N/A")
        record.user_id = getattr(record, "user_id", "N/A")
        return True


logger.addFilter(ContextFilter())

router = APIRouter(prefix="/vehicles")


@router.get(
    "/{vehicle_id}/autocheck/",
    summary="Get AutoCheck data for a vehicle",
    description="Retrieve the AutoCheck data including screenshot URL for a specific vehicle by its ID.",
)
async def get_autocheck(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """
    Retrieve AutoCheck data for a specific vehicle.

    Args:
        vehicle_id (int): The ID of the vehicle to fetch AutoCheck data for.
        db (AsyncSession): The database session dependency.
        request (Request, optional): The FastAPI request object for context.

    Returns:
        dict: The AutoCheck data including screenshot URL.

    Raises:
        HTTPException: 404 if AutoCheck data is not found for the given vehicle ID.
    """
    request_id = str(id(request))  # Generate a unique request ID
    extra = {"request_id": request_id, "user_id": "N/A"}  # UserID is N/A for now
    logger.info(f"Fetching AutoCheck data for ID: {vehicle_id}", extra=extra)
    try:
        async with db.begin():
            result = await db.execute(select(AutoCheckModel).where(AutoCheckModel.car_id == vehicle_id))
            autocheck = result.scalars().first()
            if not autocheck:
                logger.warning(f"AutoCheck data with ID {vehicle_id} not found", extra=extra)
                raise HTTPException(status_code=404, detail="AutoCheck data not found")
            logger.info(f"AutoCheck data fetched successfully for ID: {vehicle_id}", extra=extra)
            return autocheck.screenshot_url
    except Exception as e:
        logger.error(f"Error fetching AutoCheck data for ID {vehicle_id}: {str(e)}", extra=extra)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get(
    "/filter-options/",
    response_model=CarFilterOptionsSchema,
    summary="Get available filter options for cars",
    description="Retrieve unique values and ranges for filtering cars (e.g., auctions, makes, models, years, mileage, accident count, owners).",
)
async def get_car_filter_options(db: AsyncSession = Depends(get_db)) -> CarFilterOptionsSchema:
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Fetching filter options for cars", extra=extra)

    try:
        async with db.begin():
            active_filter = CarModel.relevance == RelevanceStatus.ACTIVE

            auction_query = select(distinct(CarModel.auction)).where(CarModel.auction.isnot(None), active_filter)
            auctions = [row[0] for row in (await db.execute(auction_query)).fetchall()]

            condition_assesstments_query = select(
                distinct(ConditionAssessmentModel.issue_description)
            ).where(ConditionAssessmentModel.issue_description.isnot(None))
            condition_assesstments = [row[0] for row in (await db.execute(condition_assesstments_query)).fetchall()]

            auction_name_query = select(distinct(CarModel.auction_name)).where(
                CarModel.auction_name.isnot(None), active_filter
            )
            auction_names = [row[0] for row in (await db.execute(auction_name_query)).fetchall()]

            query = select(CarModel.make, CarModel.model).where(
                CarModel.make.isnot(None), CarModel.model.isnot(None), active_filter
            ).distinct()
            make_model_map = defaultdict(list)
            for make, model in (await db.execute(query)).fetchall():
                make_model_map[make].append(model)
            make_model_map = dict(make_model_map)

            transmission_query = select(distinct(CarModel.transmision)).where(
                CarModel.transmision.isnot(None), active_filter
            )
            transmissions = [row[0] for row in (await db.execute(transmission_query)).fetchall()]

            body_style_query = select(distinct(CarModel.body_style)).where(
                CarModel.body_style.isnot(None), active_filter
            )
            body_styles = [row[0] for row in (await db.execute(body_style_query)).fetchall()]

            vehicle_type_query = select(distinct(CarModel.vehicle_type)).where(
                CarModel.vehicle_type.isnot(None), active_filter
            )
            vehicle_types = [row[0] for row in (await db.execute(vehicle_type_query)).fetchall()]

            fuel_type_query = select(distinct(CarModel.fuel_type)).where(
                CarModel.fuel_type.isnot(None), active_filter
            )
            fuel_types = [row[0] for row in (await db.execute(fuel_type_query)).fetchall()]

            drive_type_query = select(distinct(CarModel.drive_type)).where(
                CarModel.drive_type.isnot(None), active_filter
            )
            drive_types = [row[0] for row in (await db.execute(drive_type_query)).fetchall()]

            condition_query = select(distinct(CarModel.condition)).where(
                CarModel.condition.isnot(None), active_filter
            )
            conditions = [row[0] for row in (await db.execute(condition_query)).fetchall()]

            engine_cylinder_query = select(distinct(CarModel.engine_cylinder)).where(
                CarModel.engine_cylinder.isnot(None), active_filter
            )
            engine_cylinders = [row[0] for row in (await db.execute(engine_cylinder_query)).fetchall()]

            location_query = select(distinct(CarModel.location)).where(
                CarModel.location.isnot(None), active_filter
            )
            locations = [row[0] for row in (await db.execute(location_query)).fetchall()]

            year_range_query = select(func.min(CarModel.year), func.max(CarModel.year)).where(active_filter)
            year_min, year_max = (await db.execute(year_range_query)).fetchone()
            year_range = {"min": year_min, "max": year_max} if year_min and year_max else None

            mileage_range_query = select(func.min(CarModel.mileage), func.max(CarModel.mileage)).where(active_filter)
            mileage_min, mileage_max = (await db.execute(mileage_range_query)).fetchone()
            mileage_range = {"min": mileage_min, "max": mileage_max} if mileage_min and mileage_max else None

            accident_range_query = select(func.min(CarModel.accident_count), func.max(CarModel.accident_count)).where(active_filter)
            accident_count_min, accident_count_max = (await db.execute(accident_range_query)).fetchone()
            accident_count_range = {"min": accident_count_min, "max": accident_count_max} if accident_count_min and accident_count_max else None

            owners_range_query = select(func.min(CarModel.owners), func.max(CarModel.owners)).where(active_filter)
            owners_min, owners_max = (await db.execute(owners_range_query)).fetchone()
            owners_range = {"min": owners_min, "max": owners_max} if owners_min and owners_max else None

        return CarFilterOptionsSchema(
            auctions=auctions,
            auction_names=auction_names,
            makes_and_models=make_model_map,
            locations=locations,
            years=year_range,
            mileage_range=mileage_range,
            accident_count_range=accident_count_range,
            owners_range=owners_range,
            condition_assesstments=condition_assesstments,
            body_styles=body_styles,
            vehicle_types=vehicle_types,
            transmissions=transmissions,
            drive_types=drive_types,
            engine_cylinders=engine_cylinders,
            fuel_types=fuel_types,
            conditions=conditions,
        )

    except Exception as e:
        logger.error(f"Error fetching filter options: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching filter options",
        )


@router.get(
    "/",
    response_model=CarListResponseSchema,
    summary="Get a list of cars",
    description="Retrieve a paginated list of cars based on various filters such as auction, location, mileage, year, make, model, and VIN.",
)
async def get_cars(
    request: Request,
    auction: Optional[str] = Query(None, description="Auction (CoPart/IAAI)"),
    auction_name: Optional[str] = Query(None, description="Auction name"),
    location: Optional[str] = Query(None, description="Location"),
    mileage_min: Optional[int] = Query(None, description="Min mileage"),
    mileage_max: Optional[int] = Query(None, description="Max mileage"),
    min_accident_count: Optional[int] = Query(None, description="Min accident count"),
    max_accident_count: Optional[int] = Query(None, description="Max accident count"),
    min_owners_count: Optional[int] = Query(None, description="Min owners count"),
    max_owners_count: Optional[int] = Query(None, description="Max owners count"),
    min_year: Optional[int] = Query(None, description="Min year"),
    max_year: Optional[int] = Query(None, description="Max year"),
    date_from: Optional[date] = Query(None, description="Date from (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Date to (YYYY-MM-DD)"),
    make: Optional[str] = Query(None, description="Make"),
    model: Optional[str] = Query(None, description="Model"),
    predicted_profit_margin_min: Optional[float] = Query(None, description="Min predicted profit margin"),
    predicted_profit_margin_max: Optional[float] = Query(None, description="Max predicted profit margin"),
    predicted_roi_min: Optional[float] = Query(None, description="Min predicted ROI"),
    predicted_roi_max: Optional[float] = Query(None, description="Max predicted ROI"),
    body_style: Optional[str] = Query(None, description="Body style (e.g., Sedan, SUV)"),
    vehicle_type: Optional[str] = Query(None, description="Body style (e.g., Sedan, SUV)"),
    transmission: Optional[str] = Query(None, description="Body style (e.g., Sedan, SUV)"),
    drive_type: Optional[str] = Query(None, description="Body style (e.g., Sedan, SUV)"),
    engine_cylinder: Optional[str] = Query(None, description="Body style (e.g., Sedan, SUV)"),
    fuel_type: Optional[str] = Query(None, description="Body style (e.g., Sedan, SUV)"),
    condition: Optional[str] = Query(None, description="Body style (e.g., Sedan, SUV)"),
    condition_assessments: Optional[str] = Query(None, description="e.g., Rear end, Burn"),
    title: Optional[str] = Query(None, description="Salvage, Clean"),
    zip_search: Optional[str] = Query(None, description="e.g., 12345;200"),
    recommended_only: Optional[bool] = Query(False, description="'true' to show only recomended vehicles"),
    vin: Optional[str] = Query(None, description="VIN-code of the car"),
    liked: bool = Query(False, description="Filter by liked cars"),
    ordering: str = Query(
        "created_at_desc",
        description="Sort vehicles by a specific field. Available options: created_at_desc, current_bid_asc, current_bid_desc, recommendation_status_asc, recommendation_status_desc, auction_date_asc, auction_date_desc",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: UserModel = Depends(get_current_user),
) -> CarListResponseSchema:
    """
    Retrieve a paginated list of cars based on filters.
    Args:
        request (Request): The FastAPI request object for context.
        auction (List[str], optional): List of auction names (e.g., CoPart, IAAI).
        auction_name (List[str], optional): List of auction names.
        location (List[str], optional): List of locations.
        mileage_min (Optional[int], optional): Minimum mileage filter.
        mileage_max (Optional[int], optional): Maximum mileage filter.
        min_accident_count (Optional[int], optional): Minimum accident count filter.
        max_accident_count (Optional[int], optional): Maximum accident count filter.
        min_year (Optional[int], optional): Minimum year filter.
        max_year (Optional[int], optional): Maximum year filter.
        make (List[str], optional): List of car makes.
        model (List[str], optional): List of car models.
        vin (Optional[str], optional): VIN code to search for a specific car.
        ordering (str): Field to sort vehicles by (default: created_at_desc).
        page (int): Page number for pagination (default: 1).
        page_size (int): Number of items per page (default: 10, max: 100).
        db (AsyncSession): The database session dependency.
        settings (Settings): Application settings dependency.

    Returns:
        CarListResponseSchema: Paginated list of cars with pagination links.

    Raises:
        HTTPException: 404 if no vehicles are found.
    """
    if zip_search:
        zip_search = zip_search.split(";")
        if len(zip_search) != 2:
            raise HTTPException(status_code=400, detail="Both ZIP & Radius arguments are required")
        zip_search[1] = int(zip_search[1])
    request_id = str(id(request))
    extra = {"request_id": request_id, "user_id": "N/A"}
    filters = {
        "auction": auction.split(",") if auction else None,
        "auction_name": auction_name.split(",") if auction_name else None,
        "location": location.split(",") if location else None,
        "mileage_min": mileage_min,
        "mileage_max": mileage_max,
        "min_accident_count": min_accident_count,
        "max_accident_count": max_accident_count,
        "min_owners_count": min_owners_count,
        "max_owners_count": max_owners_count,
        "min_year": min_year,
        "max_year": max_year,
        "date_from": date_from,
        "date_to": date_to,
        "make": make.split(",") if make else None,
        "model": model.split(",") if model else None,
        "liked": liked,
        "predicted_profit_margin_min": predicted_profit_margin_min,
        "predicted_profit_margin_max": predicted_profit_margin_max,
        "predicted_roi_min": predicted_roi_min,
        "predicted_roi_max": predicted_roi_max,
        "user_id": current_user.id if current_user else None,
        "body_style": body_style.split(",") if body_style else None,
        "vehicle_type": vehicle_type.split(",") if vehicle_type else None,
        "transmission": transmission.split(",") if transmission else None,
        "drive_type": drive_type.split(",") if drive_type else None,
        "engine_cylinder": [int(c) for c in engine_cylinder.split(",")] if engine_cylinder else None,
        "fuel_type": fuel_type.split(",") if fuel_type else None,
        "condition": condition.split(",") if condition else None,
        "condition_assessments": condition_assessments.split(",") if condition_assessments else None,
        "title": title.split(",") if title else None,
        "zip_search": zip_search if zip_search else None,
        "recommended_only": recommended_only,
    }
    logger.info(f"Fetching cars with filters: {filters}, page: {page}, page_size: {page_size}", extra=extra)
    if vin and len(vin.replace(" ", "")) == 17:
        vin = vin.replace(" ", "")
        logger.info(f"Searching for vehicle with VIN: {vin}", extra=extra)
        async with db.begin():
            vehicle = await get_vehicle_by_vin(db, vin, current_user.id if current_user else None)
            if vehicle:
                logger.info(f"Found vehicle with VIN: {vin}", extra=extra)
                vehicle_data = car_to_dict(vehicle)
                vehicle_data["liked"] = vehicle.liked
                validated_vehicle = CarBaseSchema.model_validate(vehicle_data)
                return CarListResponseSchema(cars=[validated_vehicle], page_links={}, last=True)
            else:
                logger.info(f"Vehicle with VIN {vin} not found in DB, attempting to scrape", extra=extra)
                validated_vehicle = await scrape_and_save_vehicle(vin, db, settings)
                vehicle = await get_vehicle_by_vin(db, vin, current_user.id if current_user else None)
                await db.commit()
                vehicle_data = car_to_dict(vehicle)
                vehicle_data["liked"] = vehicle.liked
                validated_vehicle = CarBaseSchema.model_validate(vehicle_data)
                logger.info(f"Scraped and saved data for VIN {vin}, returning response", extra=extra)
                return CarListResponseSchema(cars=[validated_vehicle], page_links={}, last=True)

    vehicles, total_count, total_pages, additional = await get_filtered_vehicles(
        db=db, filters=filters, ordering=ordering, page=page, page_size=page_size
    )
    if not vehicles:
        logger.info("No vehicles found with the given filters", extra=extra)
        return CarListResponseSchema(cars=[], page_links={}, last=True)
    base_url = str(request.url.remove_query_params("page"))
    response = await prepare_response(vehicles, total_pages, page, base_url, additional)
    logger.info(f"Returning {len(response.cars)} cars, total pages: {total_pages}", extra=extra)
    return response


@router.get(
    "/{car_id}/",
    response_model=CarDetailResponseSchema,
    summary="Get detailed information for a car",
    description="Retrieve detailed information for a specific car by its ID.",
)
async def get_car_detail(
    car_id: int, db: AsyncSession = Depends(get_db), current_user: UserModel = Depends(get_current_user)
) -> CarDetailResponseSchema:
    """
    Retrieve detailed information for a specific car.

    Args:
        car_id (int): The ID of the car to fetch details for.
        db (AsyncSession): The database session dependency.
        settings (Settings): Application settings dependency.

    Returns:
        CarDetailResponseSchema: Detailed car information.

    Raises:
        HTTPException: 404 if the car is not found.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Fetching details for car with ID: {car_id}", extra=extra)

    car = await get_vehicle_by_id(db, car_id, current_user.id)
    if not car:
        logger.warning(f"Car with ID {car_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Car not found")

    logger.info(f"Returning details for car with ID: {car_id}", extra=extra)
    logger.info(f"Car condition: {car.condition_assessments}", extra=extra)
    return await prepare_car_detail_response(car)


@router.patch(
    "/cars/{car_id}",
    status_code=200,
    summary="Update car",
    description="Update car fields; when avg_market_price is provided, recompute related pricing fields."
)
async def update_car(
    car_id: int,
    data: CarUpdateSchema,
    session: AsyncSession = Depends(get_db),
    user: UserModel = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Update a car. If `avg_market_price` is provided, recompute:
      - predicted_total_investments = avg_market_price / (1 + ROI/100)
      - predicted_profit_margin_percent = default ROI profit margin
      - predicted_profit_margin = avg_market_price * (profit_margin/100)
      - auction_fee from matched FeeModel rows (same logic as before)
      - suggested_bid = predicted_total_investments - sum_of_investments

    Notes:
    - Keeps original business logic; fixes route, session.get usage, None checks and auction_fee assignment.
    """
    # Fetch car (proper AsyncSession.get signature)
    car = await session.get(CarModel, car_id)
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")

    # Recompute block if avg_market_price provided
    if data.avg_market_price is not None:
        # Get most recent ROI row
        roi_stmt = (
            select(ROIModel)
            .order_by(ROIModel.created_at.desc())
            .limit(1)
        )
        roi_row = await session.execute(roi_stmt)
        default_roi = roi_row.scalars().first()
        if default_roi is None:
            # Can't recompute without ROI baseline
            raise HTTPException(status_code=400, detail="Default ROI baseline not found")

        # Assign incoming price
        car.avg_market_price = data.avg_market_price

        # Core formulas (unchanged)
        car.predicted_total_investments = car.avg_market_price / (1 + default_roi.roi / 100.0)
        car.predicted_profit_margin_percent = default_roi.profit_margin
        car.predicted_profit_margin = car.avg_market_price * (default_roi.profit_margin / 100.0)

        # Fees lookup (unchanged logic, still uses *0.8 window)
        fees_stmt = (
            select(FeeModel)
            .where(
                FeeModel.auction == car.auction,
                FeeModel.price_from <= (car.predicted_total_investments or 0.0) * 0.8,
                FeeModel.price_to   >= (car.predicted_total_investments or 0.0) * 0.8,
            )
        )
        fee_rows = (await session.execute(fees_stmt)).scalars().all()

        fee_total = 0.0
        base_for_percent = (car.predicted_total_investments or 0.0) * 0.8
        for fee in fee_rows:
            if fee.percent:
                fee_total += (float(fee.amount) / 100.0) * base_for_percent
            else:
                fee_total += float(fee.amount)

        car.auction_fee = float(fee_total)

        # Keep original suggested_bid logic
        car.suggested_bid = int(
            (car.predicted_total_investments or 0.0) - (car.sum_of_investments or 0.0)
        )

    # Optional status update
    if data.recommendation_status is not None:
        car.recommendation_status = data.recommendation_status

    await session.commit()
    await session.refresh(car)

    # Lightweight response without changing logic/contracts
    return {
        "message": "Car updated",
        "car_id": car_id,
        "avg_market_price": car.avg_market_price,
        "predicted_total_investments": float(car.predicted_total_investments or 0.0),
        "predicted_profit_margin_percent": float(car.predicted_profit_margin_percent or 0.0),
        "predicted_profit_margin": float(car.predicted_profit_margin or 0.0),
        "auction_fee": float(car.auction_fee or 0.0),
        "suggested_bid": int(car.suggested_bid or 0),
        "recommendation_status": getattr(car, "recommendation_status", None),
    }


@router.post("/cars/{car_id}/scrape")
async def scrape_wehicle_by_id(
    car_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    # Fetch car (proper AsyncSession.get signature)
    car = await session.get(CarModel, car_id)
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")
    celery_app.send_task(
        "tasks.task.parse_and_update_car",
        kwargs={
            "vin": car.vin,
            "car_name": car.vehicle,
            "car_engine": car.engine_title,
            "mileage": car.mileage,
            "car_make": car.make,
            "car_model": car.model,
            "car_year": car.year,
            "car_transmison": car.transmision,
        },
        queue="car_parsing_queue",)
    return

@router.patch("/cars/{car_id}/check")
async def update_car_is_checked(
    car_id: int,
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(select(CarModel).where(CarModel.id == car_id))
    car = result.scalar_one_or_none()

    if not car:
        raise HTTPException(status_code=404, detail="Car not found")


    car.is_checked = not car.is_checked
    session.add(car)
    await session.commit()
    await session.refresh(car)

    return {"car_id": car.id, "is_checked": car.is_checked}


@router.put(
    "/{car_id}/status/",
    response_model=UpdateCarStatusSchema,
    summary="Update car status",
    description="Update the status of a specific car by its ID.",
)
async def update_car_status(
    car_id: int,
    status_data: UpdateCarStatusSchema,
    db: AsyncSession = Depends(get_db),
    current_user: Settings = Depends(get_current_user),
):
    """
    Update the status of a specific car.

    Args:
        car_id (int): The ID of the car to update.
        status_data (UpdateCarStatusSchema): The new status data.
        db (AsyncSession): The database session dependency.
        current_user (Settings): The current user dependency.

    Returns:
        UpdateCarStatusSchema: The updated status data.

    Raises:
        HTTPException: 404 if the car is not found.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Updating status for car with ID: {car_id}, new status: {status_data.car_status}", extra=extra)

    car, old_status = await update_vehicle_status(db, car_id, status_data.car_status)
    if not car:
        logger.warning(f"Car with ID {car_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Car not found")
    hub_history = HistoryModel(
        car_id=car_id,
        action=f"Status changed from {old_status} to {status_data.car_status.value}",
        user_id=current_user.id,
        comment=status_data.comment,
    )
    db.add(hub_history)
    await db.commit()

    logger.info(f"Status updated for car with ID: {car_id}", extra=extra)
    return status_data


@router.put(
    "/cars/{car_id}/costs",
    summary="Update Car Costs",
    description="""
Updates specific cost fields (maintenance, transportation, labor) for a car identified by VIN.

### Available Fields:
- **maintenance**: Optional float value for maintenance costs
- **transportation**: Optional float value for transportation costs
- **labor**: Optional float value for labor costs

Only provided fields will be updated; others remain unchanged.
""",
)
async def update_car_costs(car_id: int, car_data: CarCostsUpdateRequestSchema, db: AsyncSession = Depends(get_db)):
    """
    Updates the specified cost fields for a car based on its ID using ORM.

    This endpoint performs a partial update, leaving unchanged fields intact.
    """
    logger.debug("Updating car costs for ID %s with data: %s", car_id, car_data.dict(exclude_unset=True))

    db_car = await db.get(CarModel, car_id)
    if not db_car:
        raise HTTPException(status_code=404, detail=f"Car with ID {car_id} not found")

    update_data = car_data.dict(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields provided for update")

    # Update fields
    for key, value in update_data.items():
        setattr(db_car, key, value)
    db_car.suggested_bid = db_car.predicted_total_investments - db_car.sum_of_investments

    try:
        await db.commit()
        await db.refresh(db_car)
        return {
            "id": db_car.id,
            "maintenance": db_car.maintenance,
            "transportation": db_car.transportation,
            "labor": db_car.labor,
            "suggested_bid": db_car.suggested_bid,
        }
    except Exception as e:
        await db.rollback()
        logger.error("Error updating car costs for ID %s: %s", car_id, str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/{vehicle_id}/parts/",
    response_model=List[PartResponseScheme],
    summary="Get parts for a vehicle",
    description="Retrieve all parts for a specific vehicle by its ID.",
)
async def get_parts_endpoint(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve all parts for a specific vehicle.

    Args:
        vehicle_id (int): The ID of the vehicle to fetch parts for.
        db (AsyncSession): The database session dependency.

    Returns:
        List[PartResponseScheme]: List of parts associated with the vehicle.

    Raises:
        HTTPException: 404 if the vehicle is not found.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Fetching parts for vehicle with ID: {vehicle_id}", extra=extra)
    parts = await get_parts_by_vehicle_id(db, vehicle_id)
    if not parts:
        logger.warning(f"Vehicle with ID {vehicle_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Vehicle not found")
    logger.info(f"Found {len(parts)} parts for vehicle with ID: {vehicle_id}", extra=extra)
    return [PartResponseScheme.model_validate(part) for part in parts]


@router.post(
    "/{vehicle_id}/parts/",
    response_model=PartResponseScheme,
    summary="Add a part to a vehicle",
    description="Add a new part to a specific vehicle by its ID.",
)
async def add_part(
    vehicle_id: int,
    part: PartRequestScheme,
    db: AsyncSession = Depends(get_db),
):
    """
    Add a new part to a specific vehicle.

    Args:
        vehicle_id (int): The ID of the vehicle to add the part to.
        part (PartRequestScheme): The part data to be added.
        db (AsyncSession): The database session dependency.

    Returns:
        PartResponseScheme: The created part data.

    Raises:
        HTTPException: 404 if the car is not found.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Adding part for car with ID: {vehicle_id}, part: {part.dict()}", extra=extra)

    new_part, car = await add_part_to_vehicle(db, vehicle_id, part.dict())
    if not new_part:
        logger.warning(f"Car with ID {vehicle_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Car not found")

    logger.info(f"Part added for car with ID: {vehicle_id}, part ID: {new_part.id}", extra=extra)
    return PartResponseScheme(
        name=new_part.name,
        value=new_part.value,
        car_id=vehicle_id,
        id=new_part.id,
        suggested_bid=car.suggested_bid,
    )


@router.put(
    "/{vehicle_id}/parts/{part_id}/",
    response_model=PartResponseScheme,
    summary="Update a part of a vehicle",
    description="Update an existing part for a specific vehicle by its ID and part ID.",
)
async def update_part_endpoint(
    vehicle_id: int,
    part_id: int,
    part: PartRequestScheme,
    db: AsyncSession = Depends(get_db),
):
    """
    Update an existing part for a specific vehicle.

    Args:
        vehicle_id (int): The ID of the vehicle.
        part_id (int): The ID of the part to update.
        part (PartRequestScheme): The updated part data.
        db (AsyncSession): The database session dependency.

    Returns:
        PartResponseScheme: The updated part data.

    Raises:
        HTTPException: 404 if the part is not found.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Updating part with ID: {part_id} for car with ID: {vehicle_id}", extra=extra)

    updated_part, car = await update_part(db, vehicle_id, part_id, part.dict())
    if not updated_part:
        logger.warning(f"Part with ID {part_id} for car with ID {vehicle_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Part not found")

    logger.info(f"Part with ID: {part_id} updated for car with ID: {vehicle_id}", extra=extra)
    return PartResponseScheme(
        name=updated_part.name,
        value=updated_part.value,
        car_id=vehicle_id,
        id=updated_part.id,
        suggested_bid=car.suggested_bid,
    )


@router.delete(
    "/{vehicle_id}/parts/{part_id}/",
    status_code=204,
    summary="Delete a part from a vehicle",
    description="Delete an existing part from a specific vehicle by its ID and part ID.",
)
async def delete_part_endpoint(
    vehicle_id: int,
    part_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete an existing part from a specific vehicle.

    Args:
        vehicle_id (int): The ID of the vehicle.
        part_id (int): The ID of the part to delete.
        db (AsyncSession): The database session dependency.

    Returns:
        dict: Success message.

    Raises:
        HTTPException: 404 if the part is not found.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Deleting part with ID: {part_id} for car with ID: {vehicle_id}", extra=extra)

    success, car = await delete_part(db, vehicle_id, part_id)
    if not success:
        logger.warning(f"Part with ID {part_id} for car with ID {vehicle_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Part not found")

    logger.info(f"Part with ID: {part_id} deleted for car with ID: {vehicle_id}", extra=extra)
    return {
        "message": "Part deleted successfully",
        "suggested_bid": car.suggested_bid,
    }


@router.post("/bulk", status_code=201, summary="Bulk create vehicles", description="Create multiple vehicles in bulk.")
async def bulk_create_cars(
    data: CarBulkCreateSchema,
    db: AsyncSession = Depends(get_db),
    token: str = Depends(get_token),
    settings: Settings = Depends(get_settings),
) -> Dict:
    """
    Create multiple vehicles in bulk.

    Args:
        vehicles (List[CarCreateSchema]): List of vehicle data to create.
        db (AsyncSession): The database session dependency.
        token (str): Authentication token dependency.

    Returns:
        Dict: Response with success message and skipped VINs if any.

    Raises:
        HTTPException: 500 if an error occurs during bulk creation.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Starting bulk creation of {len(data.vehicles)} vehicles", extra=extra)

    try:
        skipped_vins = await bulk_save_vehicles(db, data)
        response = {"message": "Cars created successfully"}
        if skipped_vins:
            response["skipped_vins"] = skipped_vins
            logger.info(
                f"Bulk creation completed, skipped {len(skipped_vins)} vehicles", extra=extra
            )
        else:
            logger.info("Bulk creation completed with no skipped vehicles", extra=extra)

        for vehicle_data in data.vehicles:
            if vehicle_data.vin not in skipped_vins:
                logger.info(f"Scheduling parse_and_update_car for VIN: {vehicle_data.vin}", extra=extra)
                celery_app.send_task(
                    "tasks.task.parse_and_update_car",
                    kwargs={
                        "vin": vehicle_data.vin,
                        "car_name": vehicle_data.vehicle,
                        "car_engine": vehicle_data.engine_title,
                        "mileage": vehicle_data.mileage,
                        "car_make": vehicle_data.make,
                        "car_model": vehicle_data.model,
                        "car_year": vehicle_data.year,
                        "car_transmison": vehicle_data.transmision,
                    },
                    queue="car_parsing_queue",)


                # await scrape_and_save_sales_history(vehicle_data.vin, db, settings)

        return response
    except Exception as e:
        logger.error(f"Error during bulk creation of vehicles: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error during bulk creation",
        )


@router.post("/bulk/delete", status_code=status.HTTP_204_NO_CONTENT, summary="Bulk delete vehicles", description="Create multiple vehicles in bulk.")
async def bulk_delete_cars(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    token: str = Depends(get_token),
    settings: Settings = Depends(get_settings),
):
    try:
        await update_cars_relevance(payload=payload, db=db)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception("Database error during bulk deletion")
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.exception("Unexpected error during bulk deletion")
        raise HTTPException(status_code=500, detail="Unexpected server error")


@router.post("/cars/{car_id}/like-toggle")
async def toggle_like(
    car_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    car_result = await db.execute(select(CarModel).where(CarModel.id == car_id))
    car = car_result.scalar_one_or_none()

    if not car:
        raise HTTPException(status_code=404, detail="Car not found")

    user_result = await db.execute(
        select(UserModel).options(selectinload(UserModel.liked_cars)).where(UserModel.id == current_user.id)
    )
    user = user_result.scalar_one()

    if car in user.liked_cars:
        user.liked_cars.remove(car)
        await db.commit()
        return {"detail": "Unliked"}
    else:
        user.liked_cars.append(car)
        await db.commit()
        return {"detail": "Liked"}

@router.post("/update-car-info/{vehicle_id}", response_model=CarListResponseSchema)
async def update_car_info(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    # 1) Дістаємо авто за PK
    vehicle = await db.get(CarModel, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail=f"Vehicle id={vehicle_id} not found")

    vin = vehicle.vin
    if not vin or len(vin) != 17:
        raise HTTPException(status_code=400, detail="Vehicle has invalid or missing VIN")

    # 2) Тягнемо дані з парсера по VIN
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}) as client:
            resp = await client.get(f"http://parsers:8001/api/v1/apicar/{vin}")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                body = None
                try:
                    body = e.response.text[:500]
                except Exception:
                    pass
                # 404 від парсера → 404 у нас; інші → 502
                if e.response is not None and e.response.status_code == 404:
                    raise HTTPException(status_code=404, detail=f"Parser: VIN {vin} not found")
                raise HTTPException(status_code=502, detail=f"Parser error {e.response.status_code}: {body}")
            payload = resp.json()
    except httpx.RequestError as e:
        # мережеві/таймаут/DNS збої
        raise HTTPException(status_code=503, detail=f"Cannot reach parser service: {e!s}")

    # 3) Валідую в DTO
    try:
        dto = CarCreateSchema.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid data from parser: {e.errors()}")

    # 4) Зберігаю/оновлюю
    saved = await save_vehicle_with_photos(dto, "update", db)
    await db.commit()
    # Для режиму update НЕ трактуємо "вже існує" як помилку; if saved is False — все одно йдемо далі

    # 5) Повертаю свіже авто по VIN
    result = await db.execute(
        select(CarModel)
        .options(selectinload(CarModel.photos))
        .where(CarModel.vin == vin)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=500, detail="Failed to retrieve saved vehicle")

    vehicle_data = car_to_dict(vehicle)
    vehicle_data["liked"] = False
    validated_vehicle = CarBaseSchema.model_validate(vehicle_data)

    return CarListResponseSchema(cars=[validated_vehicle], page_links={})
