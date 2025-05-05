import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from schemas.vehicle import (
    CarBiddinHubResponseSchema,
    CarBiddinHubListResponseSchema,
    BiddingHubHistoryListResponseSchema,
    BiddingHubHistorySchema,
    UpdateCurrentBidSchema,
)
from models.user import UserModel
from models.vehicle import CarStatus
from schemas.user import UserResponseSchema
from core.config import Settings
from core.dependencies import get_current_user
from db.session import get_db
from crud.vehicle import get_vehicle_by_id, update_vehicle_status, get_bidding_hub_vehicles
from models.vehicle import BiddingHubHistoryModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

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
        description="Field to sort by: vehicle, auction, location, date, lot, avg_market_price, user, status",
        regex="^(vehicle|auction|location|date|lot|avg_market_price|user|status)$",
    ),
    sort_order: str = Query("desc", description="Sort order: asc or desc", regex="^(asc|desc)$"),
    current_user: Settings = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CarBiddinHubListResponseSchema:
    """
    Get paginated list of vehicles in the bidding hub.
    """
    logger.info(
        f"Fetching bidding hub vehicles (page={page}, page_size={page_size}, sort_by={sort_by}, sort_order={sort_order}) for user_id={current_user.id}"
    )

    try:
        vehicles, total_count, total_pages = await get_bidding_hub_vehicles(
            db, page=page, page_size=page_size, current_user=current_user, sort_by=sort_by, sort_order=sort_order
        )
        logger.info(f"Found {len(vehicles)} vehicles, total_count={total_count}, total_pages={total_pages}")
        logger.info(f"user_first_name: {vehicles[0].bidding_hub_history[0].user.first_name}")
        return CarBiddinHubListResponseSchema(
            vehicles=[CarBiddinHubResponseSchema.from_orm(vehicle) for vehicle in vehicles],
            total_count=total_count,
            total_pages=total_pages,
        )
    except Exception as e:
        logger.error(f"Error fetching bidding hub vehicles: {str(e)}")
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
    """
    logger.info(f"Deleting vehicle with car_id={car_id} from bidding hub for user_id={current_user.id}")

    try:
        vehicle = await update_vehicle_status(db, car_id, CarStatus.DELETED_FROM_BIDDING_HUB)
        if not vehicle:
            logger.error(f"Vehicle with car_id={car_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

        hub_history = BiddingHubHistoryModel(
            car_id=car_id,
            action="Deleted vehicle from Bidding Hub",
            user_id=current_user.id,
        )
        db.add(hub_history)
        await db.commit()
        logger.info(f"Successfully deleted vehicle with car_id={car_id} and logged history")
    except Exception as e:
        logger.error(f"Error deleting vehicle with car_id={car_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting vehicle from bidding hub",
        )


@router.post(
    "/current_bid/{car_id}",
    status_code=status.HTTP_200_OK,
    summary="Update current bid for a vehicle",
    description="Update the current bid for a vehicle in the bidding hub and log the action in history.",
)
async def update_current_bid(
    car_id: int,
    data: UpdateCurrentBidSchema,
    current_user: Settings = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update the current bid for a vehicle in the bidding hub.
    """
    logger.info(f"Updating current bid for car_id={car_id} to {data.current_bid} by user_id={current_user.id}")

    try:
        vehicle = await get_vehicle_by_id(db, car_id)
        if not vehicle:
            logger.error(f"Vehicle with car_id={car_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

        hub_history = BiddingHubHistoryModel(
            car_id=car_id,
            action=f"Updated current bid from {vehicle.current_bid} to {data.current_bid}",
            user_id=current_user.id,
            comment=data.comment,
        )
        db.add(hub_history)
        vehicle.current_bid = data.current_bid
        await db.commit()
        await db.refresh(vehicle)
        logger.info(f"Successfully updated current bid for car_id={car_id} and logged history")
    except Exception as e:
        logger.error(f"Error updating current bid for car_id={car_id}: {str(e)}")
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
    """
    logger.info(f"Fetching bidding history for car_id={car_id} by user_id={current_user.id}")

    try:
        vehicle = await get_vehicle_by_id(db, car_id)
        if not vehicle:
            logger.error(f"Vehicle with car_id={car_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")

        stmt = (
            select(BiddingHubHistoryModel)
            .where(BiddingHubHistoryModel.car_id == car_id)
            .options(selectinload(BiddingHubHistoryModel.user).selectinload(UserModel.role))
            .order_by(BiddingHubHistoryModel.created_at.desc())
        )
        result = await db.execute(stmt)
        history_list = result.scalars().all()
        logger.info(f"Found {len(history_list)} history records for car_id={car_id}")

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching bidding history for car_id={car_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching bidding history",
        )
