import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_token
from schemas.schemas import DCResponseSchema
from services.convert import format_car_data
from services.parsers.dc_scraper_local import DealerCenterScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["auction_io"])


@router.get(
    "/auction_io/get/{car_vin}",
    description="Get data from auction_io",
)
async def get_sales_history(car_vin: str):
    """
    Get data from auction_io.
    """
    logger.info(f"Received request to fetch sales history for VIN: {car_vin}")

    url = f"https://apiauctions.io/api/v1/get-car-vin?api_token={os.getenv("AUCTION_IO_KEY")}&vin_number={car_vin}&only_with_color=0"
    logger.debug(f"Fetching data from URL: {url}")

    try:
        response = httpx.post(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch API data for VIN {car_vin}: {e}")
        return None

    formatted_vehicle = format_car_data(data.get("result")[0])
    adapted_vehicle = {
        "vin": formatted_vehicle["vin"],
        "vehicle": formatted_vehicle.get("vehicle"),
        "year": formatted_vehicle.get("year"),
        "mileage": formatted_vehicle.get("mileage"),
        "auction": formatted_vehicle.get("auction"),
        "auction_name": formatted_vehicle.get("auction_name"),
        "date": formatted_vehicle.get("date").isoformat() if formatted_vehicle.get("date") else None,
        "lot": formatted_vehicle.get("lot"),
        "seller": formatted_vehicle.get("seller"),
        "location": formatted_vehicle.get("location"),
        "bid": formatted_vehicle.get("bid"),
        "engine": formatted_vehicle.get("engine"),
        "has_keys": formatted_vehicle.get("has_keys"),
        "engine_cylinder": formatted_vehicle.get("engine_cylinder"),
        "drive_type": formatted_vehicle.get("drive_type"),
        "exterior_color": formatted_vehicle.get("exterior_color"),
        "body_style": formatted_vehicle.get("body_style"),
        "transmision": formatted_vehicle.get("transmision"),
        "vehicle_type": formatted_vehicle.get("vehicle_type"),
        "is_salvage": formatted_vehicle.get("is_salvage", False),
        "photos": formatted_vehicle.get("photos", []),
        "sales_history": formatted_vehicle.get("sales_history", []),
        "current_bid": formatted_vehicle.get("current_bid", 0),
    }
    logger.info(f"SALESSSSS {adapted_vehicle.get('sales_history')}")

    logger.info(f"Successfully processed data for VIN {car_vin}")
    return adapted_vehicle
