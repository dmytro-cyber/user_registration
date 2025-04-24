from celery import Celery
from celery.schedules import crontab
import httpx
import os
import json
from dotenv import load_dotenv
from services.convert.vehicle import format_car_data


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

# @app.task
# def fetch_api_data():
    
#     url = "https://api.apicar.store/api/cars?size=30&transmission=Automatic&status=Run%20%26%20Drive&odometer_min=50000&odometer_max=100000&sort=created_at&direction=DESC"
#     print("Fetching data from API...")
#     try:
#         response = httpx.get(url, timeout=10, headers={"api-key": os.getenv("APICAR_KEY")})
#         response.raise_for_status()
#         data = response.json().get("data")

#         processed_vehicles = []
#         for vehicle in data:
#             formatted_vehicle = format_car_data(vehicle)
#             adapted_vehicle = {
#                 "vin": formatted_vehicle["vin"],
#                 "vehicle": formatted_vehicle["vehicle"],
#                 "year": formatted_vehicle.get("year"),
#                 "mileage": formatted_vehicle.get("mileage"),
#                 "auction": formatted_vehicle.get("auction"),
#                 "auction_name": formatted_vehicle.get("auction_name"),
#                 "date": formatted_vehicle.get("date").isoformat() if formatted_vehicle.get("date") else None,
#                 "lot": formatted_vehicle.get("lot"),
#                 "seller": formatted_vehicle.get("seller"),
#                 "location": formatted_vehicle.get("location"),
#                 "bid": formatted_vehicle.get("bid"),
#                 "engine": formatted_vehicle.get("engine"),
#                 "has_keys": formatted_vehicle.get("has_keys"),
#                 "engine_cylinder": formatted_vehicle.get("engine_cylinder"),
#                 "drive_type": formatted_vehicle.get("drive_type"),
#                 "exterior_color": formatted_vehicle.get("exterior_color"),
#                 "body_style": formatted_vehicle.get("body_style"),
#                 "transmision": formatted_vehicle.get(
#                     "transmision"
#                 ),
#                 "vehicle_type": formatted_vehicle.get("vehicle_type"),
#                 "is_salvage": formatted_vehicle.get("is_salvage", False),
#                 "photos": formatted_vehicle.get("photos", []),
#             }
#             processed_vehicles.append(adapted_vehicle)
#         return data
#     except httpx.HTTPError as e:
#         print(f"Failed to fetch API data: {e}")
#         return None


@app.task
def fetch_api_data():
    """
    Load saved API response from iaai_response_ex.json and process it.

    Returns:
        List containing the processed data, or None if an error occurs.
    """
    file_path = os.path.join("iaai_response_ex.json")

    try:
        with open(file_path, "r") as file:
            data = json.load(file)

        data = data.get("data")
        if not data:
            return None

        processed_vehicles = []
        for vehicle in data:
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
                "transmision": formatted_vehicle.get(
                    "transmision"
                ),
                "vehicle_type": formatted_vehicle.get("vehicle_type"),
                "is_salvage": formatted_vehicle.get("is_salvage", False),
                "photos": formatted_vehicle.get("photos", []),
            }
            processed_vehicles.append(adapted_vehicle)

        httpx_client = httpx.Client(timeout=5.0)
        httpx_client.headers.update({"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")})
        response = httpx_client.post(f"http://entities:8000/api/v1/vehicles/bulk/", json=processed_vehicles)
        print(f"Received response: {response.status_code} - {response.text}")
        return processed_vehicles

    except FileNotFoundError:
        print(f"File {file_path} not found.")
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON from file: {e}")
        return None
    except httpx.RequestError as e:
        print(f"HTTP request failed: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None
