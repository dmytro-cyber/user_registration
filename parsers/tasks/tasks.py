import json
import logging
import os
import time
from datetime import datetime
from itertools import islice
from typing import Optional
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
    "fetch-api-data-every-hour": {
        "task": "tasks.tasks.fetch_api_data",
        "schedule": crontab(minute="*/60"),
    },
    "delete-vehicles-evry-hour-at-0:15": {
        "task": "tasks.tasks.delete_vehicle",
        "schedule": crontab(minute=15)
    }
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

def _ts() -> str:
    """Compact timestamp for folder names: YYYYMMDD_HHMMSS_mmm."""
    now = datetime.now()
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{int(now.microsecond/1000):03d}"

def chunked(iterable, size):
    """Yield successive chunks of a given size from iterable."""
    it = iter(iterable)
    for first in it:
        yield [first, *islice(it, size - 1)]

@app.task
def fetch_api_data(size: Optional[int] = None, base_url: Optional[str] = None):
    """
    Fetch pages from APICAR one-by-one, save raw JSON responses to disk,
    then read those saved files and forward vehicles to Entities in batches.

    IMPORTANT:
    - We do NOT modify data processing: format_car_data() + the existing mapping
      remain exactly as before.
    - The only change is the source of truth: we read from saved files instead
      of the live APICAR response.
    """
    # Defaults (kept from your original task)
    if not base_url:
        base_url = "https://api.apicar.store/api/cars/db/update"
    if not size:
        size = 1000

    # Where to keep APICAR raw responses for this run
    root_dir = os.path.abspath("./apicar_runs")
    run_dir = os.path.join(root_dir, f"run_{_ts()}")
    pages_dir = os.path.join(run_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    # Headers for Entities and APICAR (unchanged)
    headers_entities = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}
    apicar_headers = {"api-key": os.getenv("APICAR_KEY")}

    page = 1
    logger.info(f"[APICAR] Start fetching to {pages_dir}. base_url={base_url} size={size}")

    # -----------------------------
    # Phase 1: Fetch & save pages
    # -----------------------------
    with httpx.Client(timeout=10) as client:
        while True:
            url = generate_car_api_url(page=page, size=size, base_url=base_url)
            logger.info(f"[APICAR] Fetch page {page}: {url}")

            try:
                resp = client.get(url, headers=apicar_headers)
                resp.raise_for_status()
                api_response = resp.json()
                data = api_response.get("data", [])
            except httpx.HTTPError as e:
                logger.error(f"[APICAR] Failed to fetch page {page}: {e}")
                # зберігаємо файл-помилку для спостережуваності і переходимо до наступної сторінки
                err_path = os.path.join(pages_dir, f"page_{page:05d}_ERROR.json")
                try:
                    with open(err_path, "w", encoding="utf-8") as fh:
                        json.dump({"error": str(e), "url": url}, fh, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                # переходимо до наступної спроби сторінки
                page += 1
                continue

            # Save the successful page response as-is
            out_path = os.path.join(pages_dir, f"page_{page:05d}.json")
            try:
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(api_response, fh, ensure_ascii=False)
                logger.info(f"[APICAR] Saved page {page} with {len(data)} records -> {out_path}")
            except Exception as e:
                logger.error(f"[APICAR] Failed to write page {page} to disk: {e}")
                break

            # Stop condition: empty page
            if not data:
                logger.info(f"[APICAR] Empty page at {page}. Stop fetching.")
                break

            page += 1

    # -----------------------------------------
    # Phase 2: Read saved pages & forward data
    # -----------------------------------------
    save_url = "http://entities:8000/api/v1/vehicles/bulk"
    # зчитуємо лише успішні сторінки
    page_files = sorted(
        f for f in os.listdir(pages_dir)
        if f.startswith("page_") and f.endswith(".json") and "_ERROR" not in f
    )

    if not page_files:
        logger.info("[APICAR] No saved pages to forward. Done.")
        return {"message": "No pages processed", "run_dir": run_dir}

    for fname in page_files:
        fpath = os.path.join(pages_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                page_payload = json.load(fh)
        except Exception as e:
            logger.error(f"[FORWARD] Failed to read {fpath}: {e}")
            continue

        data = page_payload.get("data", [])
        if not data:
            # nothing to push for this page
            continue

        # === your original processing stays intact ===
        processed_vehicles = []
        for vehicle in data:
            try:
                formatted_vehicle = format_car_data(vehicle)  # <--- as before
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
            except Exception:
                # keep prior “silently skip broken record” behavior
                pass

        if not processed_vehicles:
            continue

        for batch in chunked(processed_vehicles, 25):
            payload = {
                "ivent": "created" if base_url else "updated",
                "vehicles": batch,
            }
            try:
                save_response = httpx.post(save_url, json=payload, headers=headers_entities, timeout=3600)
                save_response.raise_for_status()
                logger.info(f"[FORWARD] Sent {len(batch)} vehicles from {fname} to entities.")
            except httpx.HTTPError as e:
                logger.error(f"[FORWARD] Failed to save vehicles from {fname}: {e}")

    logger.info(f"[DONE] Finished forwarding. Run folder: {run_dir}")
    return {"message": "Finished processing all saved pages", "run_dir": run_dir}


@app.task
def delete_vehicle():
    headers = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}
    url = "https://api.apicar.store/api/cars/deleted"
    response = httpx.get(url, timeout=1000, headers={"api-key": os.getenv("APICAR_KEY")})
    to_delete = response.json()
    delete_url = "http://entities:8000/api/v1/vehicles/bulk/delete"
    httpx.post(delete_url, json=to_delete, headers=headers)