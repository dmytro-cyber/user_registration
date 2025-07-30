import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

from services.convert.vehicle import format_car_data

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

CREATED_AT = None

# Celery configuration
app = Celery(
    "tasks",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/0",
)

# Celery beat configuration
app.conf.beat_schedule = {
    "fetch-api-data-every-minute": {
        "task": "tasks.tasks.fetch_api_data",
        "schedule": crontab(minute="*/60"),
    },
}

app.conf.timezone = "UTC"


def generate_car_api_url(page: int = 1, size: int = 1000, base_url: str = "https://api.apicar.store/api/cars/db/update") -> str:
    """
    Generates a URL for the API request based on the provided response data, including pagination.

    Args:
        data (dict): Data containing make, model, year_from, year_to, odometer_min, odometer_max.
        page (int): The page number to include in the URL (default is 1).

    Returns:
        str: The generated URL.
    """
    base_url = base_url
    fixed_params = {
        "size": size,
        "page": page,
    }

    all_params = {**fixed_params}
    query_string = urlencode(all_params, safe="&")
    return f"{base_url}?{query_string}"


@app.task
def fetch_api_data(size: int = None, base_url: str = None):
    """
    Fetches car data from the API for each filter, paginates through results, and saves data incrementally.
    Updates the filter's updated_at field with the first created_at value from the API response.
    Stops fetching when a car's created_at is earlier than the filter's updated_at.
    """

    headers = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}

    while True:
        url = generate_car_api_url(page=page, size=size, base_url=base_url)
        logger.info(f"Fetching data from API (page {page}): {url}")
        try:
            response = httpx.get(url, timeout=10, headers={"api-key": os.getenv("APICAR_KEY")})
            response.raise_for_status()
            api_response = response.json()
            data = api_response.get("data", [])
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch API data on page {page}: {e}")
            break
        finally:
            time.sleep(1)
        if not data:
            logger.info(f"No more data on page {page}.")
            break
        # Process vehicles on the current page
        processed_vehicles = []
        for vehicle in data:
            # created_at_str = vehicle.get("created_at")
            # if not created_at_str:
            #     continue
            # # Parse created_at to datetime
            # try:
            #     created_at_str = created_at_str.replace("Z", "+00:00")
            #     created_at = datetime.fromisoformat(created_at_str)
            # except ValueError as e:
            #     logger.error(f"Invalid created_at format for vehicle: {e}")
            #     continue
            # if first_created_at is None and page == 1:
            #     first_created_at = created_at

            formatted_vehicle = format_car_data(vehicle)
            adapted_vehicle = {
                "vin": formatted_vehicle["vin"],
                "vehicle": formatted_vehicle["vehicle"],
                "make": formatted_vehicle["make"],
                "model": formatted_vehicle["model"],
                "year": formatted_vehicle.get("year"),
                "mileage": formatted_vehicle.get("mileage"),
                "auction": formatted_vehicle.get("auction"),
                "auction_name": formatted_vehicle.get("auction_name"),
                "date": formatted_vehicle.get("date").isoformat() if formatted_vehicle.get("date") else None,
                "lot": formatted_vehicle.get("lot"),
                "seller": formatted_vehicle.get("seller"),
                "seller_type": formatted_vehicle.get("seller_type"),
                "location": formatted_vehicle.get("location"),
                "current_bid": formatted_vehicle.get("current_bid"),
                "engine": formatted_vehicle.get("engine"),
                "has_keys": formatted_vehicle.get("has_keys"),
                "engine_title": formatted_vehicle.get("engine_title"),
                "engine_cylinder": formatted_vehicle.get("engine_cylinder"),
                "drive_type": formatted_vehicle.get("drive_type"),
                "exterior_color": formatted_vehicle.get("exterior_color"),
                "condition": formatted_vehicle.get("condition"),
                "body_style": formatted_vehicle.get("body_style"),
                "fuel_type": formatted_vehicle.get("fuel_type"),
                "transmision": formatted_vehicle.get("transmision"),
                "vehicle_type": formatted_vehicle.get("vehicle_type"),
                "link": formatted_vehicle.get("link"),
                "is_salvage": formatted_vehicle.get("is_salvage", False),
                "photos": formatted_vehicle.get("photos", []),
                "photos_hd": formatted_vehicle.get("photos_hd", []),
                "condition_assessments": formatted_vehicle.get("condition_assessments", []),
            }
            processed_vehicles.append(adapted_vehicle)
        
        payload = {
            "ivent": "created" if base_url is not None else "updated",
            "vehicles": processed_vehicles
        }

        if processed_vehicles:
            save_url = "http://entities:8000/api/v1/vehicles/bulk"
            try:
                save_response = httpx.post(save_url, json=payload, headers=headers, timeout=20)
                save_response.raise_for_status()
                logger.info(
                    f"Successfully saved {len(processed_vehicles)} vehicles on page {page}"
                )
            except httpx.HTTPError as e:
                logger.error(f"Failed to save vehicles on page {page}: {e}")
                break
        page += 1

    logger.info("Finished processing all pages.")
    return "Finished processing all pages."


# @app.task
# def fetch_api_data():
#     """
#     Load saved API response from iaai_response_ex.json and process it.

#     Returns:
#         List containing the processed data, or None if an error occurs.
#     """
#     file_path = os.path.join("iaai_response_ex.json")

#     try:
#         with open(file_path, "r") as file:
#             data = json.load(file)

#         data = data.get("data")
#         if not data:
#             return None

#         asd = []
#         processed_vehicles = []
#         for vehicle in data:
#             formatted_vehicle = format_car_data(vehicle)
#             asd.append(
#                 {
#                     "vin": formatted_vehicle["vin"],
#                     "vehicle": formatted_vehicle["vehicle"],
#                     "engine": formatted_vehicle["engine"],
#                 }
#             )
# adapted_vehicle = {
#     "vin": formatted_vehicle["vin"],
#     "vehicle": formatted_vehicle["vehicle"],
#     "make": formatted_vehicle["make"],
#     "model": formatted_vehicle["model"],
#     "year": formatted_vehicle.get("year"),
#     "mileage": formatted_vehicle.get("mileage"),
#     "auction": formatted_vehicle.get("auction"),
#     "auction_name": formatted_vehicle.get("auction_name"),
#     "date": formatted_vehicle.get("date").isoformat() if formatted_vehicle.get("date") else None,
#     "lot": formatted_vehicle.get("lot"),
#     "seller": formatted_vehicle.get("seller"),
#     "location": formatted_vehicle.get("location"),
#     "bid": formatted_vehicle.get("bid"),
#     "engine": formatted_vehicle.get("engine"),
#     "has_keys": formatted_vehicle.get("has_keys"),
#     "engine_cylinder": formatted_vehicle.get("engine_cylinder"),
#     "drive_type": formatted_vehicle.get("drive_type"),
#     "exterior_color": formatted_vehicle.get("exterior_color"),
#     "body_style": formatted_vehicle.get("body_style"),
#     "transmision": formatted_vehicle.get("transmision"),
#     "vehicle_type": formatted_vehicle.get("vehicle_type"),
#     "link": formatted_vehicle.get("link"),
#     "is_salvage": formatted_vehicle.get("is_salvage", False),
#     "photos": formatted_vehicle.get("photos", []),
#     "photos_hd": formatted_vehicle.get("photos_hd", []),
#     "condition_assessments": formatted_vehicle.get("condition_assessments", []),
# }
# processed_vehicles.append(adapted_vehicle)

#         print(f"Processed vehicles: {asd}")
#         httpx_client = httpx.Client(timeout=5.0)
#         httpx_client.headers.update({"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")})
#         response = httpx_client.post(f"http://entities:8000/api/v1/vehicles/bulk/", json=processed_vehicles)
#         print(f"Received response: {response.status_code} - {response.text}")
#         return processed_vehicles

#     except FileNotFoundError:
#         print(f"File {file_path} not found.")
#         return None
#     except json.JSONDecodeError as e:
#         print(f"Failed to parse JSON from file: {e}")
#         return None
#     except httpx.RequestError as e:
#         print(f"HTTP request failed: {e}")
#         return None
#     except Exception as e:
#         print(f"An unexpected error occurred: {e}")
#         return None
