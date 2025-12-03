import logging
from celery import Celery
from celery.schedules import crontab
import httpx
import os
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from services.convert import format_car_data
from urllib.parse import urlencode
import time

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
        "task": "tasks.tasks_auction_io.fetch_api_data",
        "schedule": crontab(minute="*/60"),
    },
}

app.conf.timezone = "UTC"


from datetime import date
from dateutil.relativedelta import relativedelta
from urllib.parse import urlencode
import os


def generate_car_api_url(data: dict, page: int = 1) -> str:
    """
    Generates a complete URL for the API request including all required query parameters.
    """

    base_url = "https://apiauctions.io/api/v2/get-active-lots"

    fixed_params = {
        "per_page": 30,
        "page": page,
    }

    dynamic_params: dict[str, object] = {}

    if data.get("make") and data["make"].strip():
        dynamic_params["make"] = data["make"]

    if data.get("model") and data["model"].strip():
        dynamic_params["model"] = data["model"]

    if data.get("year_from"):
        dynamic_params["year_from"] = data["year_from"]

    if data.get("year_to"):
        dynamic_params["year_to"] = data["year_to"]

    dynamic_params["api_token"] = os.getenv("AUCTION_IO_KEY")
    dynamic_params["auction_names[]"] = ["COPART"]
    dynamic_params["auction_name"] = "IAAI"


    dynamic_params["auction_date_from"] = date.today().strftime("%Y-%m-%d")
    dynamic_params["auction_date_to"] = (date.today() + relativedelta(months=2)).strftime("%Y-%m-%d")

    dynamic_params["current_bid_from"] = 0
    dynamic_params["current_bid_to"] = 200000

    dynamic_params["is_buy_now"] = 0
    dynamic_params["buy_now_price_from"] = 0
    dynamic_params["buy_now_price_to"] = 100000

    dynamic_params["estimate_retail_from"] = 1
    dynamic_params["estimate_retail_to"] = 100000


    dynamic_params["without_sale_date"] = 0

    all_params = {**fixed_params, **dynamic_params}

    query_string = urlencode(all_params, doseq=True)

    return f"{base_url}?{query_string}"



@app.task
def fetch_api_data():
    """
    Fetches car data from the API for each filter, paginates through results, and saves data incrementally.
    Updates the filter's updated_at field with the first created_at value from the API response.
    Stops fetching when a car's created_at is earlier than the filter's updated_at.
    """
    # Fetch filters from the API
    logger.info("Fetching filters from the API...")
    filters_url = "http://entities:8000/api/v1/admin/filters"
    headers = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}
    try:
        response = httpx.get(filters_url, timeout=10, headers=headers)
        logger.info(f"Response status code: {response.status_code}")
        response.raise_for_status()
        filters = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch filters: {e}")
        return None

    if not filters:
        logger.warning("No filters found.")
        return None

    # Process each filter
    for filter_data in filters:
        filter_id = filter_data.get("id")

        page = 1
        first_created_at = None
        stop_fetching = False

        # Paginate through the API results
        while True:
            # Generate URL with the current page
            url = generate_car_api_url(filter_data, page=page)
            logger.info(f"Fetching data from API (page {page}) for filter {filter_id}: {url}")

            try:
                response = httpx.post(url, timeout=10)
                api_response = response.json()
                logger.info(api_response)
                response.raise_for_status()
                api_response = response.json()
                data = api_response.get("result", [])
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch API data for filter {filter_id} on page {page}: {e}")
                break
            finally:
                time.sleep(1)

            if not data:
                logger.info(f"No more data for filter {filter_id} on page {page}.")
                break

            # Process vehicles on the current page
            processed_vehicles = []
            for vehicle in data:
                created_at_str = vehicle.get("created_at")
                if not created_at_str:
                    continue

                # Parse created_at to datetime
                try:
                    created_at_str = created_at_str.replace("Z", "+00:00")
                    created_at = datetime.fromisoformat(created_at_str)
                except ValueError as e:
                    logger.error(f"Invalid created_at format for vehicle: {e}")
                    continue

                # Store the first created_at value (from the first page)
                if first_created_at is None and page == 1:
                    first_created_at = created_at


                # Format and adapt vehicle data
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
                    "location": formatted_vehicle.get("location"),
                    "current_bid": formatted_vehicle.get("current_bid"),
                    "engine": formatted_vehicle.get("engine"),
                    "has_keys": formatted_vehicle.get("has_keys"),
                    "engine_title": formatted_vehicle.get("engine_title"),
                    "engine_cylinder": formatted_vehicle.get("engine_cylinder"),
                    "drive_type": formatted_vehicle.get("drive_type"),
                    "exterior_color": formatted_vehicle.get("exterior_color"),
                    "body_style": formatted_vehicle.get("body_style"),
                    "transmision": formatted_vehicle.get("transmision"),
                    "vehicle_type": formatted_vehicle.get("vehicle_type"),
                    "link": formatted_vehicle.get("link"),
                    "is_salvage": formatted_vehicle.get("is_salvage", False),
                    "photos": formatted_vehicle.get("photos", []),
                    "photos_hd": formatted_vehicle.get("photos_hd", []),
                    "condition_assessments": formatted_vehicle.get("condition_assessments", []),
                }
                processed_vehicles.append(adapted_vehicle)

            if stop_fetching:
                break

            # Save processed vehicles incrementally via POST request
            if processed_vehicles:
                save_url = "http://entities:8000/api/v1/vehicles/bulk"
                try:
                    save_response = httpx.post(save_url, json={"ivent": "update", "vehicles": processed_vehicles}, headers=headers, timeout=10)
                    save_response.raise_for_status()
                    logger.info(
                        f"Successfully saved {len(processed_vehicles)} vehicles for filter {filter_id} on page {page}"
                    )
                except httpx.HTTPError as e:
                    logger.error(f"Failed to save vehicles for filter {filter_id} on page {page}: {e}")
                    break

            page += 1

    logger.info("Finished processing all filters.")
    return "Finished processing all filters."
