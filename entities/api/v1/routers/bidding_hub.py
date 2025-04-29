import asyncio
import logging
from fastapi import APIRouter, Depends, Query, Request, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.vehicle import (
    CarBiddinHubResponseSchema,
    CarBiddinHubListResponseSchema
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


router = APIRouter(prefix="/bidding_hub")


@router.get("/", response_model=CarBiddinHubListResponseSchema)
async def get_bidding_hub(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    current_user: Settings = Depends(get_token),
    db: AsyncSession = Depends(get_db),
) -> CarBiddinHubListResponseSchema:
    """
    Get paginated list of vehicles.
    """

    vehicles, total_count, total_pages = await get_filtered_vehicles(
        db, {"bidding_hub": True}, page, page_size
    )

    return CarBiddinHubListResponseSchema(
        vehicles=[CarBiddinHubResponseSchema.model_validate(vehicle) for vehicle in vehicles],
        total_count=total_count,
        total_pages=total_pages
    )


@router.delete("/delete/{car_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    car_id: int,
    current_user: Settings = Depends(get_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a vehicle from bidding hub by ID.
    """
    vehicle = await update_vehicle_status(db, car_id, "Deleted from Bidding Hub")
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    await db.commit()

    return

