from fastapi import APIRouter, Depends, HTTPException, status, Query, File, UploadFile
from fastapi.responses import JSONResponse
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from datetime import datetime

from models.admin import FilterModel, ROIModel
from schemas.admin import (
    FilterCreate,
    FilterUpdate,
    FilterResponse,
    FilterUpdateTimestamp,
    ROICreateSchema,
    ROIResponseSchema,
    ROIListResponseSchema,
)
from db.session import get_db
from core.dependencies import get_token, get_current_user
import logging
import logging.handlers
import os

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


# Create a new filter
@router.post("/filters", response_model=FilterResponse, status_code=status.HTTP_201_CREATED)
async def create_filter(
    filter: FilterCreate, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """
    Create a new filter.

    Args:
        filter (FilterCreate): The data for the new filter.
        current_user: The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        FilterResponse: The created filter.

    Raises:
        HTTPException: 500 if an error occurs during filter creation.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Creating new filter by user_id={current_user.id}", extra=extra)

    try:
        db_filter = FilterModel(**filter.dict(exclude_unset=True))
        db_filter.updated_at = datetime.utcnow()
        db.add(db_filter)
        await db.commit()
        await db.refresh(db_filter)
        logger.info(f"Filter created successfully with id={db_filter.id}", extra=extra)
        return db_filter
    except Exception as e:
        logger.error(f"Failed to create filter: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating filter",
        )


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
@router.patch("/filters/{filter_id}", response_model=FilterResponse)
async def update_filter(
    filter_id: int,
    filter_update: FilterUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a filter (partial update).

    Args:
        filter_id (int): The ID of the filter to update.
        filter_update (FilterUpdate): The data to update the filter with.
        current_user: The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        FilterResponse: The updated filter.

    Raises:
        HTTPException: 404 if the filter is not found.
        HTTPException: 500 if an error occurs during update.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Updating filter with id={filter_id} by user_id={current_user.id}", extra=extra)

    try:
        result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
        db_filter = result.scalars().first()
        if not db_filter:
            logger.warning(f"Filter with id={filter_id} not found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

        update_data = filter_update.dict(exclude_unset=True)
        for key, value in update_data.items():
            if value:
                setattr(db_filter, key, value)

        db_filter.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(db_filter)
        logger.info(f"Filter with id={filter_id} updated successfully", extra=extra)
        return db_filter
    except HTTPException as e:
        logger.error(f"Failed to update filter with id={filter_id}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while updating filter with id={filter_id}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating filter",
        )


@router.patch("/filters/{filter_id}/timestamp")
async def update_filter_timestamp(
    filter_id: int, update_data: FilterUpdateTimestamp, db: AsyncSession = Depends(get_db)
):
    """
    Update the timestamp of a filter.

    Args:
        filter_id (int): The ID of the filter to update.
        update_data (FilterUpdateTimestamp): The new timestamp data.
        db (AsyncSession): The database session dependency.

    Returns:
        FilterModel: The updated filter.

    Raises:
        HTTPException: 404 if the filter is not found.
        HTTPException: 500 if an error occurs during update.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": "N/A"}
    logger.info(f"Updating timestamp for filter with id={filter_id}", extra=extra)

    try:
        result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
        db_filter = result.scalars().first()
        if not db_filter:
            logger.warning(f"Filter with id={filter_id} not found", extra=extra)
            raise HTTPException(status_code=404, detail="Filter not found")

        updated_at_naive = update_data.updated_at.replace(tzinfo=None)
        db_filter.updated_at = updated_at_naive
        await db.commit()
        await db.refresh(db_filter)
        logger.info(f"Timestamp updated successfully for filter with id={filter_id}", extra=extra)
        return db_filter
    except HTTPException as e:
        logger.error(f"Failed to update timestamp for filter with id={filter_id}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error while updating timestamp for filter with id={filter_id}: {str(e)}", extra=extra
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating filter timestamp",
        )


# Delete a filter
@router.delete("/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(filter_id: int, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """
    Delete a filter.

    Args:
        filter_id (int): The ID of the filter to delete.
        current_user: The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Raises:
        HTTPException: 404 if the filter is not found.
        HTTPException: 500 if an error occurs during deletion.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Deleting filter with id={filter_id} by user_id={current_user.id}", extra=extra)

    try:
        result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
        db_filter = result.scalars().first()
        if not db_filter:
            logger.warning(f"Filter with id={filter_id} not found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

        await db.delete(db_filter)
        await db.commit()
        logger.info(f"Filter with id={filter_id} deleted successfully", extra=extra)
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
async def proxy_upload(file1: UploadFile = File(...), file2: UploadFile = File(...)):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            files = {
                "file1": (file1.filename, await file1.read(), file1.content_type),
                "file2": (file2.filename, await file2.read(), file2.content_type),
            }

            response = await client.post("http://parsers:8001/api/v1/parsers/svrape/iaai/fees", files=files)

            return JSONResponse(
                status_code=response.status_code,
                content={"message": f"Forwarded successfully", "external_status": response.status_code, "original_response": response.json()},
            )

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact external service: {str(e)}")
