import json
import logging
import os
import shutil
import time
from datetime import datetime
from itertools import islice
from typing import Optional
from urllib.parse import urlencode

import httpx
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

from services.convert.vehicle import format_car_data

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

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
    """Generate URL for APICAR API with pagination."""
    fixed_params = {"size": size, "page": page}
    query_string = urlencode(fixed_params, safe="&")
    return f"{base_url}?{query_string}"


def _ts() -> str:
    """Compact timestamp for folder names."""
    now = datetime.now()
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{int(now.microsecond/1000):03d}"


def chunked(iterable, size):
    """Yield successive chunks of a given size from iterable."""
    it = iter(iterable)
    for first in it:
        yield [first, *islice(it, size - 1)]


@app.task
def fetch_api_data(size: Optional[int] = None, base_url: Optional[str] = None):
    """Fetch ALL pages -> save -> process ALL -> cleanup folder"""

    if not base_url:
        base_url = "https://api.apicar.store/api/cars/db/update"
    if not size:
        size = 1000

    root_dir = os.path.abspath("./apicar_runs")
    run_dir = os.path.join(root_dir, f"run_{_ts()}")
    pages_dir = os.path.join(run_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    headers_entities = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}
    apicar_headers = {"api-key": os.getenv("APICAR_KEY")}
    save_url = "http://entities:8000/api/v1/vehicles/bulk"

    page = 1
    logger.info(f"[APICAR] Start bulk fetch. base_url={base_url} size={size}")

    # -------------------------------------------------
    # 1) FETCH ALL PAGES
    # -------------------------------------------------
    with httpx.Client(timeout=20) as client:
        while True:
            url = generate_car_api_url(page=page, size=size, base_url=base_url)
            logger.info(f"[APICAR] Fetch page {page}: {url}")

            try:
                resp = client.get(url, headers=apicar_headers)
                resp.raise_for_status()
                payload = resp.json()
                data = payload.get("data", [])
            except httpx.HTTPError as e:
                logger.error(f"[APICAR] Page {page} failed: {e}")
                break

            if not data:
                logger.info(f"[APICAR] No data at page {page}. Stop.")
                break

            out_path = os.path.join(pages_dir, f"page_{page:05d}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

            logger.info(f"[APICAR] Saved page {page} ({len(data)} records)")
            page += 1

    # -------------------------------------------------
    # 2) PROCESS ALL FILES
    # -------------------------------------------------
    logger.info("[PROCESS] Start processing saved pages")

    files = sorted(os.listdir(pages_dir))
    for fname in files:
        fpath = os.path.join(pages_dir, fname)

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                page_payload = json.load(f)
        except Exception as e:
            logger.error(f"[PROCESS] Failed read {fname}: {e}")
            continue

        vehicles = []
        for raw in page_payload.get("data", []):
            try:
                formatted = format_car_data(raw)
                vehicles.append({
                    "vin": formatted["vin"],
                    "vehicle": formatted["vehicle"],
                    "make": formatted["make"],
                    "model": formatted["model"],
                    "year": formatted.get("year"),
                    "mileage": formatted.get("mileage"),
                    "auction": formatted.get("auction"),
                    "auction_name": formatted.get("auction_name"),
                    "date": formatted.get("date").isoformat() if formatted.get("date") else None,
                    "lot": formatted.get("lot"),
                    "seller": formatted.get("seller"),
                    "seller_type": formatted.get("seller_type"),
                    "location": formatted.get("location"),
                    "current_bid": formatted.get("current_bid"),
                    "engine": formatted.get("engine"),
                    "has_keys": formatted.get("has_keys"),
                    "engine_title": formatted.get("engine_title"),
                    "engine_cylinder": formatted.get("engine_cylinder"),
                    "drive_type": formatted.get("drive_type"),
                    "exterior_color": formatted.get("exterior_color"),
                    "condition": formatted.get("condition"),
                    "body_style": formatted.get("body_style"),
                    "fuel_type": formatted.get("fuel_type"),
                    "transmision": formatted.get("transmision"),
                    "vehicle_type": formatted.get("vehicle_type"),
                    "link": formatted.get("link"),
                    "is_salvage": formatted.get("is_salvage", False),
                    "photos": formatted.get("photos", []),
                    "photos_hd": formatted.get("photos_hd", []),
                    "condition_assessments": formatted.get("condition_assessments", []),
                })
            except Exception:
                continue

        if vehicles:
            for batch in chunked(vehicles, 25):
                payload = {
                    "ivent": "updated" if size == 1000 else "created",
                    "vehicles": batch,
                }
                try:
                    r = httpx.post(save_url, json=payload, headers=headers_entities, timeout=3600)
                    r.raise_for_status()
                    logger.info(f"[FORWARD] Sent {len(batch)} vehicles from {fname}")
                except httpx.HTTPError as e:
                    logger.error(f"[FORWARD] Failed {fname}: {e}")

    # -------------------------------------------------
    # 3) FULL CLEANUP
    # -------------------------------------------------
    try:
        shutil.rmtree(run_dir)
        logger.info(f"[CLEANUP] Removed {run_dir}")
    except Exception as e:
        logger.warning(f"[CLEANUP] Cleanup failed: {e}")

    return {"message": "Finished bulk fetch+process", "pages": page - 1}



@app.task
def delete_vehicle():
    """Delete outdated vehicles."""
    headers = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}
    url = "https://api.apicar.store/api/cars/deleted"
    response = httpx.get(url, timeout=1000, headers={"api-key": os.getenv("APICAR_KEY")})
    to_delete = response.json()
    delete_url = "http://entities:8000/api/v1/vehicles/bulk/delete"
    httpx.post(delete_url, json=to_delete, headers=headers)
