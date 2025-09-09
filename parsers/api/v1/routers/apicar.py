import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query, HTTPException

from core.dependencies import get_token
from schemas.schemas import DCResponseSchema
from services.convert.vehicle import format_car_data
from services.parsers.dc_scraper_local import DealerCenterScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["apicar"])


@router.get(
    "/apicar/get/{car_vin}",
    description="Get data from apicar",
)
async def get_sales_history(car_vin: str):
    """
    Get data from apicar.
    """
    logger.info(f"Received request to fetch sales history for VIN: {car_vin}")

    url = f"https://api.apicar.store/api/sale-histories/vin?vin={car_vin}"
    logger.debug(f"Fetching data from URL: {url}")

    try:
        response = httpx.get(url, timeout=10, headers={"api-key": os.getenv("APICAR_KEY")})
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch API data for VIN {car_vin}: {e}")
        return None

    formatted_vehicle = format_car_data(data.get("data"))
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


@router.get(
    "/apicar/update/{car_vin}",
    description="Get data from apicar",
)
async def get_update(car_vin: str):
    """
    Get data from apicar.
    """
    logger.info(f"Received request to fetch sales history for VIN: {car_vin}")

    url = f"https://api.apicar.store/api/cars/vin/all?vin={car_vin}"
    logger.debug(f"Fetching data from URL: {url}")

    try:
        response = httpx.get(url, timeout=10, headers={"api-key": os.getenv("APICAR_KEY")})
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch API data for VIN {car_vin}: {e}")
        raise HTTPException(status_code=404, detail=f"VIN {car_vin} not found")

    formatted_vehicle = format_car_data(data[0])
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
