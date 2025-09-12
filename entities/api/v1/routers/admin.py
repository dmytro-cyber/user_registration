import logging
import logging.handlers
import os
import re
from datetime import datetime
from typing import List

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from celery.result import AsyncResult
import redis
from core.celery_config import app as celery_app
from core.dependencies import get_current_user, get_token
from db.session import get_db
from models.admin import FilterModel, ROIModel
from models.vehicle import FeeModel, CarModel, RelevanceStatus
from core.dependencies import get_settings
from services.vehicle import scrape_and_save_sales_history,build_car_filter_query
from schemas.admin import (
    FilterCreate,
    FilterResponse,
    FilterUpdate,
    FilterUpdateTimestamp,
    ROICreateSchema,
    ROIListResponseSchema,
    ROIResponseSchema,
)
# from tasks.task import parse_and_update_car

# Configure logging for production environment
logger = logging.getLogger("admin_router")
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
#     filename="logs/admin.log",
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

router = APIRouter(prefix="/admin")


REDIS_HOST = "redis_1"
REDIS_PORT = 6380
REDIS_DB   = 1

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

LOCK_KEY = "kickoff_task_lock"
LOCK_TTL_SEC = 60 * 60 * 2

def is_kickoff_busy() -> bool:
    task_id = r.get(LOCK_KEY)
    if not task_id:
        return False
    state = AsyncResult(task_id, app=celery_app).state
    if state in ("SUCCESS", "FAILURE", "REVOKED"):
        r.delete(LOCK_KEY)
        return False
    return True

def set_kickoff_lock(task_id: str) -> None:
    r.set(LOCK_KEY, task_id, ex=LOCK_TTL_SEC)

@router.post("/filters", response_model=FilterResponse, status_code=status.HTTP_201_CREATED)
async def create_filter(
    filter: FilterCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings = Depends(get_settings),
):
    if is_kickoff_busy():
        raise HTTPException(
            status_code=409,
            detail="Previous kickoff task is still running. Please wait until it finishes.",
        )

    db_filter = FilterModel(**filter.dict(exclude_unset=True))
    db_filter.updated_at = datetime.utcnow()
    db.add(db_filter)

    conditions = [
        CarModel.make == db_filter.make,
        CarModel.year >= (db_filter.year_from or 0),
        CarModel.year <= (db_filter.year_to or 3000),
        CarModel.mileage >= (db_filter.odometer_min or 0),
        CarModel.mileage <= (db_filter.odometer_max or 10_000_000),
    ]
    if db_filter.model is not None:
        conditions.append(CarModel.model == db_filter.model)

    bulk_update_stmt = (
        update(CarModel)
        .where(and_(*conditions))
        .values(relevance=RelevanceStatus.ACTIVE)
        .execution_options(synchronize_session=False)
    )
    await db.execute(bulk_update_stmt)
    await db.commit()
    await db.refresh(db_filter)

    kickoff_result = celery_app.send_task(
        "tasks.task.kickoff_parse_for_filter",
        kwargs={"filter_id": db_filter.id},
        queue="car_parsing_queue",
    )
    set_kickoff_lock(kickoff_result.id)

    return db_filter


# Get all filters
@router.get("/filters", response_model=List[FilterResponse])
async def get_filters(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """
    Get all filters with pagination.

    Args:
        skip (int): Number of records to skip (default: 0).
        limit (int): Maximum number of records to return (default: 100).
        db (AsyncSession): The database session dependency.

    Returns:
        List[FilterResponse]: List of filters.

    Raises:
        HTTPException: 500 if an error occurs during fetch.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Fetching filters (skip={skip}, limit={limit})", extra=extra)

    try:
        result = await db.execute(select(FilterModel).offset(skip).limit(limit))
        filters = result.scalars().all()
        logger.info(f"Retrieved {len(filters)} filters", extra=extra)
        return filters
    except Exception as e:
        logger.error(f"Failed to fetch filters: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching filters",
        )


# Get a single filter by ID
@router.get("/filters/{filter_id}", response_model=FilterResponse)
async def get_filter(filter_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get a single filter by ID.

    Args:
        filter_id (int): The ID of the filter to retrieve.
        db (AsyncSession): The database session dependency.

    Returns:
        FilterResponse: The requested filter.

    Raises:
        HTTPException: 404 if the filter is not found.
        HTTPException: 500 if an error occurs during fetch.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Fetching filter with id={filter_id}", extra=extra)

    try:
        result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
        filter = result.scalars().first()
        if not filter:
            logger.warning(f"Filter with id={filter_id} not found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
        logger.info(f"Filter with id={filter_id} retrieved successfully", extra=extra)
        return filter
    except HTTPException as e:
        logger.error(f"Failed to fetch filter with id={filter_id}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching filter with id={filter_id}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching filter",
        )


# Update a filter (partial update)
@router.patch("/filters/{filter_id}", summary="Update filter and car relevance")
async def update_filter_and_relevance(
    filter_id: int,
    payload: FilterUpdate,
    db: AsyncSession = Depends(get_db),
):
    if is_kickoff_busy():
        raise HTTPException(
            status_code=409,
            detail="Previous kickoff task is still running. Please wait until it finishes.",
        )

    result = await db.execute(select(FilterModel).where(FilterModel.id == filter_id))
    db_filter = result.scalar_one_or_none()
    if db_filter is None:
        raise HTTPException(status_code=404, detail="Filter not found")

    old_filter_query = build_car_filter_query(db_filter)
    old_ids_stmt = select(CarModel.id).where(*old_filter_query)
    old_car_ids = {row[0] for row in (await db.execute(old_ids_stmt)).all()}

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(db_filter, key, value)
    await db.commit()
    await db.refresh(db_filter)

    new_filter_query = build_car_filter_query(db_filter)
    new_ids_stmt = select(CarModel.id).where(*new_filter_query)
    new_car_ids = {row[0] for row in (await db.execute(new_ids_stmt)).all()}

    to_activate = new_car_ids - old_car_ids
    to_irrelevant = old_car_ids - new_car_ids

    if to_activate:
        await db.execute(
            update(CarModel)
            .where(CarModel.id.in_(to_activate), CarModel.relevance == RelevanceStatus.IRRELEVANT)
            .values(relevance=RelevanceStatus.ACTIVE)
        )

    if to_irrelevant:
        await db.execute(
            update(CarModel)
            .where(CarModel.id.in_(to_irrelevant), CarModel.relevance == RelevanceStatus.ACTIVE)
            .values(relevance=RelevanceStatus.IRRELEVANT)
        )
        await db.execute(
            delete(CarModel)
            .where(CarModel.id.in_(to_irrelevant), CarModel.relevance == RelevanceStatus.ARCHIVAL)
        )

    await db.commit()

    kickoff_result = celery_app.send_task(
        "tasks.task.kickoff_parse_for_filter",
        kwargs={"filter_id": db_filter.id},
        queue="car_parsing_queue",
    )
    set_kickoff_lock(kickoff_result.id)

    return {"detail": "Filter updated, relevance adjusted, kickoff scheduled"}


# Delete a filter
@router.delete("/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(
    filter_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a filter and update/archive matched cars.

    1. Find IDs of cars matching the filter.
    2. For archived cars — delete them.
    3. For active cars — mark them as 'irrelevant'.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Deleting filter with id={filter_id} by user_id={current_user.id}", extra=extra)

    try:
        result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
        db_filter = result.scalars().first()

        if not db_filter:
            logger.warning(f"Filter with id={filter_id} not found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

        base_filter = and_(
            CarModel.make == db_filter.make,
            CarModel.model == db_filter.model,
            CarModel.year >= (db_filter.year_from or 0),
            CarModel.year <= (db_filter.year_to or 3000),
            CarModel.mileage >= (db_filter.odometer_min or 0),
            CarModel.mileage <= (db_filter.odometer_max or 10_000_000),
            CarModel.user_id == current_user.id,
        )

        archived_query = select(CarModel.id).where(
            and_(base_filter, CarModel.relevance == RelevanceStatus.ARCHIVAL)
        )
        archived_ids = (await db.execute(archived_query)).scalars().all()

        if archived_ids:
            await db.execute(delete(CarModel).where(CarModel.id.in_(archived_ids)))

        active_query = select(CarModel.id).where(
            and_(base_filter, CarModel.relevance == RelevanceStatus.ACTIVE)
        )
        active_ids = (await db.execute(active_query)).scalars().all()

        if active_ids:
            await db.execute(
                update(CarModel)
                .where(CarModel.id.in_(active_ids))
                .values(relevance=RelevanceStatus.IRRELEVANT)
            )

        await db.delete(db_filter)
        await db.commit()

        logger.info(
            f"Filter {filter_id} deleted. Cars affected: {len(archived_ids)} archived removed, {len(active_ids)} marked irrelevant",
            extra=extra,
        )

    except HTTPException as e:
        logger.error(f"Failed to delete filter with id={filter_id}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while deleting filter with id={filter_id}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting filter",
        )


@router.get("/roi", response_model=ROIListResponseSchema)
async def get_roi(db: AsyncSession = Depends(get_db)) -> ROIListResponseSchema:
    """
    Get all ROI records.

    Args:
        db (AsyncSession): The database session dependency.

    Returns:
        ROIListResponseSchema: List of ROI records.

    Raises:
        HTTPException: 404 if no ROI records are found.
        HTTPException: 500 if an error occurs during fetch.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Fetching all ROI records", extra=extra)

    try:
        result = await db.execute(select(ROIModel).order_by(ROIModel.id.desc()))
        roi = result.scalars().all()
        if not roi:
            logger.warning("No ROI records found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ROI not found")
        logger.info(f"Retrieved {len(roi)} ROI records", extra=extra)
        return ROIListResponseSchema(roi=[ROIResponseSchema.model_validate(roi_item) for roi_item in roi])
    except HTTPException as e:
        logger.error(f"Failed to fetch ROI records: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching ROI records: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching ROI records",
        )


@router.get("/roi/calculate", response_model=ROIResponseSchema)
async def calculate_roi(
    roi: float = Query(None, description="ROI value to calculate"),
    db: AsyncSession = Depends(get_db),
) -> ROIResponseSchema:
    """
    Calculate ROI based on the provided data.

    Args:
        roi (ROICreateSchema): The data for ROI calculation.
        db (AsyncSession): The database session dependency.

    Returns:
        ROIResponseSchema: The calculated ROI record.

    Raises:
        HTTPException: 400 if the ROI value is negative.
        HTTPException: 500 if an error occurs during calculation.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Calculating ROI with roi={roi}", extra=extra)
    try:
        if roi < 0:
            logger.warning(f"Invalid ROI value: {roi} (must be >= 0)", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="ROI must be greater than or equal to 0"
            )
        roi = round(roi, 2)
        roi_model = ROIModel(roi=roi)
        roi_model.validate_and_set_profit_margin("roi", roi)
        logger.info(f"Calculated profit margin: {roi_model.profit_margin}", extra=extra)
        return roi_model
    except Exception as e:
        logger.error(f"Unexpected error while calculating ROI: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching latest ROI record",
        )


@router.get("/roi/latest", response_model=ROIResponseSchema)
async def get_latest_roi(db: AsyncSession = Depends(get_db)) -> ROIResponseSchema:
    """
    Get the latest ROI record.

    Args:
        db (AsyncSession): The database session dependency.

    Returns:
        ROIResponseSchema: The latest ROI record.

    Raises:
        HTTPException: 404 if no ROI records are found.
        HTTPException: 500 if an error occurs during fetch.
    """
    request_id = "N/A"
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info("Fetching the latest ROI record", extra=extra)
    try:
        result = await db.execute(select(ROIModel).order_by(ROIModel.id.desc()).limit(1))
        roi = result.scalars().first()
        if not roi:
            logger.warning("No ROI records found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ROI not found")
        logger.info(f"Latest ROI record retrieved successfully with id={roi.id}", extra=extra)
        return roi
    except HTTPException as e:
        logger.error(f"Failed to fetch latest ROI record: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching latest ROI record: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching latest ROI record",
        )


@router.post("/roi", response_model=ROIResponseSchema, status_code=status.HTTP_201_CREATED)
async def create_roi(roi: ROICreateSchema, db: AsyncSession = Depends(get_db)) -> ROIResponseSchema:
    """
    Create a new ROI record.

    Args:
        roi (ROICreateSchema): The data for the new ROI record.
        db (AsyncSession): The database session dependency.

    Returns:
        ROIResponseSchema: The created ROI record.

    Raises:
        HTTPException: 400 if the ROI value is negative.
        HTTPException: 500 if an error occurs during creation.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Creating new ROI record with roi={roi.roi}", extra=extra)

    try:
        if roi.roi < 0:
            logger.warning(f"Invalid ROI value: {roi.roi} (must be >= 0)", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="ROI must be greater than or equal to 0"
            )
        roi.roi = round(roi.roi, 2)
        db_roi = ROIModel(**roi.dict(exclude_unset=True))
        db.add(db_roi)
        await db.commit()
        await db.refresh(db_roi)
        logger.info(f"ROI record created successfully with id={db_roi.id}", extra=extra)
        return db_roi
    except HTTPException as e:
        logger.error(f"Failed to create ROI record: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while creating ROI record: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating ROI record",
        )


@router.post("/upload-iaai-fees")
async def proxy_upload(
    high_volume: UploadFile = File(...), internet_bid: UploadFile = File(...), db: AsyncSession = Depends(get_db)
):  # Використовуйте існуючу сесію
    try:
        # Perform HTTP request to the external endpoint
        async with httpx.AsyncClient(timeout=30) as client:
            files = {
                "high_volume": (high_volume.filename, await high_volume.read(), high_volume.content_type),
                "internet_bid": (internet_bid.filename, await internet_bid.read(), internet_bid.content_type),
            }
            response = await client.post("http://parsers:8001/api/v1/parsers/scrape/iaai/fees", files=files)

            # Log response for debugging
            logger.info(f"Response from external service: status={response.status_code}, body={response.text}")

            # Extract the response directly
            data = response.json()
            fees_data = data["fees"]

            # Update fees in the database using the provided session
            try:
                # Delete all existing fees for auction 'iaai'
                await db.execute(delete(FeeModel).where(FeeModel.auction == "iaai"))
                logger.info("Deleted all existing fees for auction 'iaai'")

                # Process different types of fees from the response
                fee_mappings = {
                    "high_volume_buyer_fees": fees_data["high_volume_buyer_fees"]["fees"],
                    "internet_bid_buyer_fees": fees_data["internet_bid_buyer_fees"]["fees"],
                    "service_fee": {"amount": fees_data["service_fee"]["amount"]},
                    "environmental_fee": {"amount": fees_data["environmental_fee"]["amount"]},
                    "title_handling_fee": {"amount": fees_data["title_handling_fee"]["amount"]},
                }

                for fee_type, fee_values in fee_mappings.items():
                    if isinstance(fee_values, dict):
                        if fee_type in ["service_fee", "environmental_fee", "title_handling_fee"]:
                            # Handle fixed fees
                            amount = float(fee_values["amount"])
                            fee = FeeModel(
                                auction="iaai",
                                fee_type=fee_type,
                                amount=amount,
                                percent=False,
                                price_from=None,
                                price_to=None,
                            )
                            db.add(fee)
                            logger.info(f"Added fee: type={fee_type}, amount={amount}, percent=False, range=None-None")
                        else:
                            # Handle fees with price ranges
                            for price_range, amount_str in fee_values.items():
                                # Extract numeric value and handle percentage case
                                is_percent = False
                                amount = amount_str
                                if isinstance(amount_str, str) and "%" in amount_str:
                                    is_percent = True
                                    # Use regex to extract the number before "%"
                                    match = re.match(r"([\d.]+)%", amount_str)
                                    if match:
                                        amount = float(match.group(1))
                                    else:
                                        raise ValueError(f"Invalid percentage format: {amount_str}")
                                else:
                                    amount = float(amount_str)

                                if "-" in price_range:  # Price range (e.g., "0.00-99.99")
                                    price_from, price_to = map(float, price_range.split("-"))
                                else:  # Single value or "15000.00+"
                                    price_from = (
                                        float(price_range.replace("+", "")) if price_range != "15000.00+" else 0.0
                                    )
                                    price_to = 1000000

                                fee = FeeModel(
                                    auction="iaai",
                                    fee_type=fee_type,
                                    amount=amount,
                                    percent=is_percent,
                                    price_from=price_from,
                                    price_to=price_to,
                                )
                                db.add(fee)
                                logger.info(
                                    f"Added fee: type={fee_type}, amount={amount}, percent={is_percent}, range={price_from}-{price_to}"
                                )

                # Commit changes to the database
                await db.commit()
                logger.info("Committed new fees for auction 'iaai'")

            except Exception as e:
                logger.error(f"Database error updating fees: {e}", exc_info=True)
                await db.rollback()
                raise

            return JSONResponse(
                status_code=response.status_code,
                content={
                    "message": "Forwarded successfully",
                    "external_status": response.status_code,
                    "response": data,
                },
            )

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact external service: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in proxy_upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/load-db")
async def load_db():
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post("http://parsers:8001/startup")