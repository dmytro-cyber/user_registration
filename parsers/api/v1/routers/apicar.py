from services.parsers.dc_scraper import DealerCenterScraper
from fastapi import APIRouter, Depends, Query
import asyncio
from core.dependencies import get_token
import httpx
import os
import json
from services.convert.vehicle import format_car_data

from schemas.schemas import DCResponseSchema


router = APIRouter(prefix="", tags=["apicar"])


@router.get(
    "/apicar/get/{car_vin}",
    description="Get data from apicar",
)
async def scrape_dc(car_vin: str):
    """
    Get data from apicar.
    """
    # url = f"https://api.apicar.store/api/car/vin/all?vin={car_vin}"
    # print("Fetching data from API...")
    # try:
    #     response = httpx.get(url, timeout=10, headers={"api-key": os.getenv("APICAR_KEY")})
    #     response.raise_for_status()
    #     data = response.json()
    # except httpx.HTTPError as e:
    #     print(f"Failed to fetch API data: {e}")
    #     return None
    # result = format_car_data(data[0])
    file_path = os.path.join("vin_all_response_ex.json")

    with open(file_path, "r") as file:
        data = json.load(file)

    vehicle = data[0]
    if not vehicle:
        print("No 'data' key found in JSON")
        return None

    print(f"Formatting vehicle with VIN: {vehicle.get('vin')}")
    formatted_vehicle = format_car_data(vehicle)
    adapted_vehicle = {
        "vin": formatted_vehicle["vin"],
        "vehicle": formatted_vehicle["vehicle"],
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
    }

    return adapted_vehicle
