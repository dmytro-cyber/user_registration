import asyncio
import logging
import logging.handlers
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import Settings
from core.dependencies import get_current_user
from crud.vehicle import get_bidding_hub_vehicles, get_vehicle_by_id, update_vehicle_status
from db.session import get_db
from models.user import UserModel
from models.vehicle import CarStatus, HistoryModel
from schemas.user import UserResponseSchema
from schemas.vehicle import (
    BiddingHubHistoryListResponseSchema,
    BiddingHubHistorySchema,
    CarBiddinHubListResponseSchema,
    CarBiddinHubResponseSchema,
    UpdateActualBidSchema,
)

# Configure logging for production environment
logger = logging.getLogger("bidding_hub_router")
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
#     filename="logs/bidding_hub.log",
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

router = APIRouter(prefix="/bidding_hub", tags=["Bidding Hub"])


@router.get(
    "/",
    response_model=CarBiddinHubListResponseSchema,
    summary="Get paginated list of vehicles in bidding hub",
    description="Retrieve a paginated list of vehicles currently in the bidding hub, with optional sorting.",
)
async def get_bidding_hub(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of vehicles per page (1 to 100)"),
    sort_by: str = Query(
        "date",
        description="Field to sort by: vehicle, auction, location, date, lot, avg_market_price, user, status, predicted_total_investments, predicted_profit_margin, predicted_roi, actual_bid, currnent_bid, suggested_bid",
    ),
    sort_order: str = Query("desc", description="Sort order: asc or desc", regex="^(asc|desc)$"),
    current_user: Settings = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CarBiddinHubListResponseSchema:
    """
    Get paginated list of vehicles in the bidding hub.

    Args:
        page (int): Page number for pagination (default: 1).
        page_size (int): Number of vehicles per page (default: 10, max: 100).
        sort_by (str): Field to sort by (default: "date").
        sort_order (str): Sort order (default: "desc").
        current_user (Settings): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        CarBiddinHubListResponseSchema: Paginated list of vehicles in the bidding hub.

    Raises:
        HTTPException: 404 if no vehicles are found in the bidding hub.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(
        f"Fetching bidding hub vehicles (page={page}, page_size={page_size}, sort_by={sort_by}, sort_order={sort_order}) for user_id={current_user.id}",
        extra=extra,
    )

    try:
        vehicles, total_count, total_pages = await get_bidding_hub_vehicles(
            db, page=page, page_size=page_size, current_user=current_user, sort_by=sort_by, sort_order=sort_order
        )
        print(vehicles)
        if not vehicles:
            logger.info("No vehicles found in the bidding hub", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No vehicles found in the bidding hub")
        logger.info(
            f"Found {len(vehicles)} vehicles, total_count={total_count}, total_pages={total_pages}", extra=extra
        )
        return CarBiddinHubListResponseSchema(
            vehicles=[CarBiddinHubResponseSchema.from_orm(vehicle) for vehicle in vehicles],
            total_count=total_count,
            total_pages=total_pages,
        )
    except HTTPException as e:
        logger.error(f"Failed to fetch bidding hub vehicles: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching bidding hub vehicles: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching bidding hub vehicles",
        )


@router.delete(
    "/delete/{car_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a vehicle from bidding hub",
    description="Delete a vehicle from the bidding hub by its ID and log the action in history.",
)
async def delete_vehicle(
    car_id: int,
    current_user: Settings = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a vehicle from bidding hub by ID.

    Args:
        car_id (int): The ID of the vehicle to delete.
        current_user (Settings): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Raises:
        HTTPException: 404 if the vehicle is not found.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Deleting vehicle with car_id={car_id} from bidding hub for user_id={current_user.id}", extra=extra)

    try:
        vehicle = await update_vehicle_status(db, car_id, CarStatus.DELETED_FROM_BIDDING_HUB)
        if not vehicle:
            logger.error(f"Vehicle with car_id={car_id} not found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
        hub_history = HistoryModel(
            car_id=car_id,
            action="Deleted vehicle from Bidding Hub",
            user_id=current_user.id,
        )
        db.add(hub_history)
        await db.commit()
        logger.info(f"Successfully deleted vehicle with car_id={car_id} and logged history", extra=extra)
    except HTTPException as e:
        logger.error(f"Failed to delete vehicle with car_id={car_id}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while deleting vehicle with car_id={car_id}: {str(e)}", extra=extra)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting vehicle",
        )


@router.post(
    "/actual-bid/{car_id}",
    status_code=status.HTTP_200_OK,
    summary="Update current bid for a vehicle",
    description="Update the current bid for a vehicle in the bidding hub and log the action in history.",
)
async def update_actual_bid(
    car_id: int,
    data: UpdateActualBidSchema,
    current_user: Settings = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update the current bid for a vehicle in the bidding hub.

    Args:
        car_id (int): The ID of the vehicle to update.
        data (UpdateCurrentBidSchema): The data containing the new bid and optional comment.
        current_user (Settings): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        dict: Confirmation message of the bid update.

    Raises:
        HTTPException: 404 if the vehicle is not found.
        HTTPException: 500 if an error occurs during the update.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(
        f"Updating current bid for car_id={car_id} to {data.actual_bid} by user_id={current_user.id}", extra=extra
    )

    try:
        vehicle = await get_vehicle_by_id(db, car_id)
        if not vehicle:
            logger.error(f"Vehicle with car_id={car_id} not found", extra=extra)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

        hub_history = HistoryModel(
            car_id=car_id,
            action=f"Updated actual bid from {vehicle.actual_bid} to {data.actual_bid}",
            user_id=current_user.id,
            comment=data.comment,
        )
        db.add(hub_history)
        vehicle.actual_bid = data.actual_bid
        db.add(vehicle)
        await db.commit()
        await db.refresh(vehicle)
        logger.info(f"Successfully updated current bid for car_id={car_id} and logged history", extra=extra)
    except Exception as e:
        logger.error(f"Error updating current bid for car_id={car_id}: {str(e)}", extra=extra)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating current bid",
        )

    return {"message": "Current bid updated successfully"}


@router.get(
    "/history/{car_id}",
    response_model=BiddingHubHistoryListResponseSchema,
    summary="Get bidding hub history for a vehicle",
    description="Retrieve the bidding hub history for a vehicle by its ID, including full user details, ordered by creation date (descending).",
)
async def get_bidding_hub_history(
    car_id: int,
    current_user: Settings = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BiddingHubHistoryListResponseSchema:
    """
    Get bidding hub history for a vehicle by ID, including full user details.

    Args:
        car_id (int): The ID of the vehicle to fetch history for.
        current_user (Settings): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        BiddingHubHistoryListResponseSchema: The history of bidding actions for the vehicle.

    Raises:
        HTTPException: 404 if no bidding history is found for the vehicle.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}
    logger.info(f"Fetching bidding history for car_id={car_id} by user_id={current_user.id}", extra=extra)

    try:
        stmt = (
            select(HistoryModel)
            .where(HistoryModel.car_id == car_id)
            .options(selectinload(HistoryModel.user).selectinload(UserModel.role))
            .order_by(HistoryModel.created_at.desc())
        )
        result = await db.execute(stmt)
        history_list = result.scalars().all()
        if not history_list:
            logger.info(f"No bidding history found for car_id={car_id}", extra=extra)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No bidding history found for this vehicle"
            )
        logger.info(f"Found {len(history_list)} history records for car_id={car_id}", extra=extra)
        return BiddingHubHistoryListResponseSchema(
            history=[
                BiddingHubHistorySchema(
                    id=item.id,
                    action=item.action,
                    user=(
                        UserResponseSchema(
                            email=item.user.email,
                            first_name=item.user.first_name,
                            last_name=item.user.last_name,
                            phone_number=item.user.phone_number,
                            date_of_birth=item.user.date_of_birth,
                            role=item.user.role.name if item.user.role else None,
                        )
                        if item.user
                        else None
                    ),
                    comment=item.comment,
                    created_at=item.created_at,
                )
                for item in history_list
            ]
        )
    except HTTPException as e:
        logger.error(f"Failed to fetch bidding history for car_id={car_id}: {str(e)}", extra=extra)
        raise
    except Exception as e:
        logger.error(f"Unexpected error while fetching bidding history for car_id={car_id}: {str(e)}", extra=extra)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching bidding history",
        )
