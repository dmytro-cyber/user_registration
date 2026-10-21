import logging
import logging.handlers
import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import redis
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.celery_config import app as celery_app
from core.dependencies import get_current_user, get_iaai_fee_source, get_settings, get_token
from db.session import get_db
from domain.iaai_fees import FeeImage, IaaiFeeSource
from domain.roi import calculate_admin_roi
from exceptions.fee_import import FeeSourceUnavailable
from exceptions.roi import NegativeROIError, ROIRecordNotFound
from exceptions.vehicle_filters import VehicleFilterNotFound
from integrations.parsing import ParsingClient
from integrations.vehicle_tasks import CeleryFilterUpdateKickoffPublisher
from models.admin import FilterModel
from models.filter_kickoff_queue import (
    FilterKickoffQueueModel,
    FilterKickoffQueueStatus,
)
from schemas.admin import (
    FilterCreate,
    FilterResponse,
    FilterUpdate,
    FilterUpdateTimestamp,
    ROICreateSchema,
    ROIListResponseSchema,
    ROIResponseSchema,
)
from services.fee_import import refresh_iaai_fees
from services.lock import (
    acquire_kickoff_lock,
    generate_lock_token,
    is_kickoff_busy,
    release_kickoff_lock,
)
from services.roi import create_admin_roi, latest_admin_roi, list_admin_roi
from services.vehicle_filters import create_filter_and_schedule, delete_filter_and_adjust, update_filter_and_schedule

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


# REDIS_HOST = "redis_1"
# REDIS_PORT = 6380
# REDIS_DB   = 1

# r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

# LOCK_KEY = "kickoff_task_lock"
# LOCK_TTL_SEC = 60 * 60

# def is_kickoff_busy() -> bool:
#     task_id = r.get(LOCK_KEY)
#     if not task_id:
#         return False
#     state = AsyncResult(task_id, app=celery_app).state
#     if state in ("SUCCESS", "FAILURE", "REVOKED"):
#         r.delete(LOCK_KEY)
#         return False
#     return True

# def set_kickoff_lock(task_id: str) -> None:
#     r.set(LOCK_KEY, task_id, ex=LOCK_TTL_SEC)

@router.post("/filters", response_model=FilterResponse, status_code=status.HTTP_201_CREATED)
async def create_filter(
    filter: FilterCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    return await create_filter_and_schedule(db, filter)


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

    try:
        return await update_filter_and_schedule(db, filter_id, payload, CeleryFilterUpdateKickoffPublisher(celery_app))
    except VehicleFilterNotFound:
        raise HTTPException(status_code=404, detail="Filter not found")



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
        result = await delete_filter_and_adjust(db, filter_id)
        logger.info(
            f"Filter {filter_id} deleted. Cars affected: {result.archived_removed} archived removed, {result.active_marked_irrelevant} marked irrelevant",
            extra=extra,
        )

    except VehicleFilterNotFound:
        logger.warning(f"Filter with id={filter_id} not found", extra=extra)
        error = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
        logger.error(f"Failed to delete filter with id={filter_id}: {str(error)}", extra=extra)
        raise error
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
        roi = await list_admin_roi(db)
        logger.info(f"Retrieved {len(roi)} ROI records", extra=extra)
        return ROIListResponseSchema(roi=[ROIResponseSchema.model_validate(roi_item) for roi_item in roi])
    except ROIRecordNotFound:
        logger.warning("No ROI records found", extra=extra)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ROI not found")
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
        roi_model = calculate_admin_roi(roi)
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
        roi = await latest_admin_roi(db)
        logger.info(f"Latest ROI record retrieved successfully with id={roi.id}", extra=extra)
        return roi
    except ROIRecordNotFound:
        logger.warning("No ROI records found", extra=extra)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ROI not found")
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
        db_roi = await create_admin_roi(db, roi.roi)
        logger.info(f"ROI record created successfully with id={db_roi.id}", extra=extra)
        return db_roi
    except NegativeROIError as exc:
        logger.warning(f"Invalid ROI value: {roi.roi} (must be >= 0)", extra=extra)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
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
    high_volume: UploadFile = File(...),
    internet_bid: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    fee_source: IaaiFeeSource = Depends(get_iaai_fee_source),
):
    """
    Forward two images to parsers app, upsert IAAI fees into DB.
    For fixed IAAI fees we store range 0..OPEN_CAP (not NULL..NULL).
    """
    try:
        high_volume_image = FeeImage(high_volume.filename, await high_volume.read(), high_volume.content_type)
        internet_bid_image = FeeImage(internet_bid.filename, await internet_bid.read(), internet_bid.content_type)
        result = await refresh_iaai_fees(db, fee_source, high_volume_image, internet_bid_image)
        return JSONResponse(
            status_code=result.status,
            content={"message": "Forwarded successfully", "external_status": result.status, "response": result.data},
        )

    except (FeeSourceUnavailable, httpx.RequestError) as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact external service: {str(e)}")
    except Exception as e:
        logger.exception("Unexpected error in proxy_upload")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/load-db")
async def load_db():
    async with ParsingClient(timeout=30) as client:
        await client.post("/startup")
