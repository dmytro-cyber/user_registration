import asyncio
import logging
import logging.handlers
import os
from fastapi import APIRouter, Depends, Query, Request, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from schemas.vehicle import (
    CarBaseSchema,
    CarListResponseSchema,
    CarDetailResponseSchema,
    UpdateCarStatusSchema,
    PartRequestScheme,
    PartResponseScheme,
    CarCreateSchema,
    CarFilterOptionsSchema,
)
from models.vehicle import CarModel
from models.user import UserModel
from sqlalchemy import select, func, distinct
from core.config import Settings
from core.dependencies import get_settings, get_token, get_current_user
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
    get_parts_by_vehicle_id,
)
from services.vehicle import (
    scrape_and_save_vehicle,
    prepare_response,
    prepare_car_detail_response,
    scrape_and_save_sales_history,
    car_to_dict,
)
from models.vehicle import HistoryModel, AutoCheckModel
from tasks.task import parse_and_update_car
from typing import List, Optional, Dict

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
    description="Retrieve unique values and ranges for filtering cars (e.g., auctions, makes, models, years, mileage, accident count).",
)
async def get_car_filter_options(db: AsyncSession = Depends(get_db)) -> CarFilterOptionsSchema:
    """
    Retrieve unique filter options for cars.

    Args:
        db (AsyncSession): The database session dependency.

    Returns:
        CarFilterOptionsSchema: A schema containing filter options like auctions, makes, models, and ranges.

    Raises:
        HTTPException: 500 if an error occurs while fetching filter options.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Fetching filter options for cars", extra=extra)

    try:
        async with db.begin():
            # Fetch unique auction values
            auction_query = select(distinct(CarModel.auction)).where(CarModel.auction.isnot(None))
            auctions_result = await db.execute(auction_query)
            auctions = [row[0] for row in auctions_result.fetchall()]

            # Fetch unique auction_name values
            auction_name_query = select(distinct(CarModel.auction_name)).where(CarModel.auction_name.isnot(None))
            auction_names_result = await db.execute(auction_name_query)
            auction_names = [row[0] for row in auction_names_result.fetchall()]

            # Fetch unique make values
            make_query = select(distinct(CarModel.make)).where(CarModel.make.isnot(None))
            makes_result = await db.execute(make_query)
            makes = [row[0] for row in makes_result.fetchall()]

            # Fetch unique model values
            model_query = select(distinct(CarModel.model)).where(CarModel.model.isnot(None))
            models_result = await db.execute(model_query)
            models = [row[0] for row in models_result.fetchall()]

            # Fetch unique location values
            location_query = select(distinct(CarModel.location)).where(CarModel.location.isnot(None))
            locations_result = await db.execute(location_query)
            locations = [row[0] for row in locations_result.fetchall()]

            # Fetch year range
            year_range_query = select(func.min(CarModel.year), func.max(CarModel.year))
            year_range_result = await db.execute(year_range_query)
            year_min, year_max = year_range_result.fetchone()
            year_range = {"min": year_min, "max": year_max} if year_min is not None and year_max is not None else None

            # Fetch mileage range
            mileage_range_query = select(func.min(CarModel.mileage), func.max(CarModel.mileage))
            mileage_range_result = await db.execute(mileage_range_query)
            mileage_min, mileage_max = mileage_range_result.fetchone()
            mileage_range = (
                {"min": mileage_min, "max": mileage_max}
                if mileage_min is not None and mileage_max is not None
                else None
            )

            # Fetch accident_count range
            accident_count_range_query = select(func.min(CarModel.accident_count), func.max(CarModel.accident_count))
            accident_count_range_result = await db.execute(accident_count_range_query)
            accident_count_min, accident_count_max = accident_count_range_result.fetchone()
            accident_count_range = (
                {"min": accident_count_min, "max": accident_count_max}
                if accident_count_min is not None and accident_count_max is not None
                else None
            )

        response = CarFilterOptionsSchema(
            auctions=auctions,
            auction_names=auction_names,
            makes=makes,
            models=models,
            locations=locations,
            years=year_range,
            mileage_range=mileage_range,
            accident_count_range=accident_count_range,
        )
        logger.info(f"Successfully fetched filter options")
        return response

    except Exception as e:
        logger.error(f"Error fetching filter options for cars: {str(e)}", extra=extra)
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
    auction: List[str] = Query(None, description="Auction (CoPart/IAAI)"),
    auction_name: List[str] = Query(None, description="Auction name"),
    location: List[str] = Query(None, description="Location"),
    mileage_min: Optional[int] = Query(None, description="Min mileage"),
    mileage_max: Optional[int] = Query(None, description="Max mileage"),
    min_accident_count: Optional[int] = Query(None, description="Min accident count"),
    max_accident_count: Optional[int] = Query(None, description="Max accident count"),
    min_year: Optional[int] = Query(None, description="Min year"),
    max_year: Optional[int] = Query(None, description="Max year"),
    make: List[str] = Query(None, description="Make"),
    model: List[str] = Query(None, description="Model"),
    vin: Optional[str] = Query(None, description="VIN-code of the car"),
    liked: bool = Query(False, description="Filter by liked cars"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: UserModel = Depends(get_current_user)
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
        page (int): Page number for pagination (default: 1).
        page_size (int): Number of items per page (default: 10, max: 100).
        db (AsyncSession): The database session dependency.
        settings (Settings): Application settings dependency.

    Returns:
        CarListResponseSchema: Paginated list of cars with pagination links.

    Raises:
        HTTPException: 404 if no vehicles are found.
    """
    request_id = str(id(request))
    extra = {"request_id": request_id, "user_id": "N/A"}
    filters = {
        "auction": auction,
        "auction_name": auction_name,
        "location": location,
        "mileage_min": mileage_min,
        "mileage_max": mileage_max,
        "min_accident_count": min_accident_count,
        "max_accident_count": max_accident_count,
        "min_year": min_year,
        "max_year": max_year,
        "make": make,
        "model": model,
        "liked": liked,
        "user_id": current_user.id if current_user else None,
    }
    logger.info(f"Fetching cars with filters: {filters}, page: {page}, page_size: {page_size}", extra=extra)

    if vin and len(vin.replace(" ", "")) == 17:
        vin = vin.replace(" ", "")
        logger.info(f"Searching for vehicle with VIN: {vin}", extra=extra)
        async with db.begin():
            vehicle = await get_vehicle_by_vin(db, vin)
            if vehicle:
                logger.info(f"Found vehicle with VIN: {vin}", extra=extra)
                vehicle_data = car_to_dict(vehicle)
                validated_vehicle = CarBaseSchema.model_validate(vehicle_data)
                return CarListResponseSchema(cars=[validated_vehicle], page_links={}, last=True)
            else:
                logger.info(f"Vehicle with VIN {vin} not found in DB, attempting to scrape", extra=extra)
                validated_vehicle = await scrape_and_save_vehicle(vin, db, settings)
                logger.info(f"Scraped and saved data for VIN {vin}, returning response", extra=extra)
                await db.commit()
                return CarListResponseSchema(cars=[validated_vehicle], page_links={}, last=True)

    vehicles, total_count, total_pages = await get_filtered_vehicles(db, filters, page, page_size)
    if not vehicles:
        logger.info("No vehicles found with the given filters", extra=extra)
        return CarListResponseSchema(cars=[], page_links={}, last=True)
    base_url = str(request.url.remove_query_params("page"))
    response = await prepare_response(vehicles, total_pages, page, base_url)
    logger.info(f"Returning {len(response.cars)} cars, total pages: {total_pages}", extra=extra)
    return response


@router.get(
    "/{car_id}/",
    response_model=CarDetailResponseSchema,
    summary="Get detailed information for a car",
    description="Retrieve detailed information for a specific car by its ID.",
)
async def get_car_detail(
    car_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
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

    car = await get_vehicle_by_id(db, car_id)
    if not car:
        logger.warning(f"Car with ID {car_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Car not found")

    if not car.sales_history:
        car = await scrape_and_save_sales_history(car, db, settings)

    logger.info(f"Returning details for car with ID: {car_id}", extra=extra)
    logger.info(f"Car condition: {car.condition_assessments}", extra=extra)
    return await prepare_car_detail_response(car)


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

    car = await update_vehicle_status(db, car_id, status_data.car_status)
    if not car:
        logger.warning(f"Car with ID {car_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Car not found")
    hub_history = HistoryModel(
        car_id=car_id,
        action=f"Status changed from {car.car_status} to {status_data.car_status}",
        user_id=current_user.id,
        comment=status_data.comment,
    )
    db.add(hub_history)
    await db.commit()

    logger.info(f"Status updated for car with ID: {car_id}", extra=extra)
    return status_data


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

    new_part = await add_part_to_vehicle(db, vehicle_id, part.dict())
    if not new_part:
        logger.warning(f"Car with ID {vehicle_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Car not found")

    logger.info(f"Part added for car with ID: {vehicle_id}, part ID: {new_part.id}", extra=extra)
    return new_part


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

    updated_part = await update_part(db, vehicle_id, part_id, part.dict())
    if not updated_part:
        logger.warning(f"Part with ID {part_id} for car with ID {vehicle_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Part not found")

    logger.info(f"Part with ID: {part_id} updated for car with ID: {vehicle_id}", extra=extra)
    return updated_part


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

    success = await delete_part(db, vehicle_id, part_id)
    if not success:
        logger.warning(f"Part with ID {part_id} for car with ID {vehicle_id} not found", extra=extra)
        raise HTTPException(status_code=404, detail="Part not found")

    logger.info(f"Part with ID: {part_id} deleted for car with ID: {vehicle_id}", extra=extra)
    return {"message": "Part deleted successfully"}


@router.post(
    "/bulk", status_code=201, summary="Bulk create vehicles", description="Create multiple vehicles in bulk."
)
async def bulk_create_cars(
    vehicles: List[CarCreateSchema],
    db: AsyncSession = Depends(get_db),
    token: str = Depends(get_token),
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
    logger.info(f"Starting bulk creation of {len(vehicles)} vehicles", extra=extra)

    try:
        skipped_vins = await bulk_save_vehicles(db, vehicles)
        response = {"message": "Cars created successfully"}
        if skipped_vins:
            response["skipped_vins"] = skipped_vins
            logger.info(
                f"Bulk creation completed, skipped {len(skipped_vins)} vehicles with VINs: {skipped_vins}", extra=extra
            )
        else:
            logger.info("Bulk creation completed with no skipped vehicles", extra=extra)

        for vehicle_data in vehicles:
            if vehicle_data.vin not in skipped_vins:
                logger.info(f"Scheduling parse_and_update_car for VIN: {vehicle_data.vin}", extra=extra)
                parse_and_update_car.delay(vehicle_data.vin, vehicle_data.vehicle, vehicle_data.engine)

        return response
    except Exception as e:
        logger.error(f"Error during bulk creation of vehicles: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error during bulk creation",
        )


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
        select(UserModel)
        .options(selectinload(UserModel.liked_cars))
        .where(UserModel.id == current_user.id)
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


