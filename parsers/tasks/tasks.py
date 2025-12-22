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
    # "fetch-api-data-every-hour": {
    #     "task": "tasks.tasks.fetch_api_data",
    #     "schedule": crontab(minute="*/60"),
    # },
    # "delete-vehicles-evry-hour-at-0:15": {
    #     "task": "tasks.tasks.delete_vehicle",
    #     "schedule": crontab(minute=15)
    # }
}

app.conf.timezone = "UTC"


# def generate_car_api_url(page: int = 1, size: int = 1000, base_url: str = "https://api.apicar.store/api/cars/db/update") -> str:
#     """Generate URL for APICAR API with pagination."""
#     fixed_params = {"size": size, "page": page}
#     query_string = urlencode(fixed_params, safe="&")
#     return f"{base_url}?{query_string}"


# def _ts() -> str:
#     """Compact timestamp for folder names."""
#     now = datetime.now()
#     return f"{now.strftime('%Y%m%d_%H%M%S')}_{int(now.microsecond/1000):03d}"


# def chunked(iterable, size):
#     """Yield successive chunks of a given size from iterable."""
#     it = iter(iterable)
#     for first in it:
#         yield [first, *islice(it, size - 1)]


# @app.task
# def fetch_api_data(size: Optional[int] = None, base_url: Optional[str] = None):
#     """Fetch, save, process, forward — and finally clean up local files."""
#     if not base_url:
#         base_url = "https://api.apicar.store/api/cars/db/update"
#     if not size:
#         size = 1000

#     root_dir = os.path.abspath("./apicar_runs")
#     run_dir = os.path.join(root_dir, f"run_{_ts()}")
#     pages_dir = os.path.join(run_dir, "pages")
#     os.makedirs(pages_dir, exist_ok=True)

#     headers_entities = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}
#     apicar_headers = {"api-key": os.getenv("APICAR_KEY")}
#     save_url = "http://entities:8000/api/v1/vehicles/bulk"

#     page = 1
#     logger.info(f"[APICAR] Start fetching to {pages_dir}. base_url={base_url} size={size}")

#     try:
#         # Phase 1 — fetch and save
#         with httpx.Client(timeout=10) as client:
#             while True:
#                 url = generate_car_api_url(page=page, size=size, base_url=base_url)
#                 logger.info(f"[APICAR] Fetch page {page}: {url}")

#                 try:
#                     resp = client.get(url, headers=apicar_headers)
#                     resp.raise_for_status()
#                     api_response = resp.json()
#                     data = api_response.get("data", [])
#                 except httpx.HTTPError as e:
#                     logger.error(f"[APICAR] Failed to fetch page {page}: {e}")
#                     err_path = os.path.join(pages_dir, f"page_{page:05d}_ERROR.json")
#                     with open(err_path, "w", encoding="utf-8") as fh:
#                         json.dump({"error": str(e), "url": url}, fh, ensure_ascii=False, indent=2)
#                     page += 1
#                     continue

#                 out_path = os.path.join(pages_dir, f"page_{page:05d}.json")
#                 with open(out_path, "w", encoding="utf-8") as fh:
#                     json.dump(api_response, fh, ensure_ascii=False)
#                 logger.info(f"[APICAR] Saved page {page} with {len(data)} records -> {out_path}")

#                 if not data:
#                     logger.info(f"[APICAR] Empty page {page}. Stop fetching.")
#                     break
#                 page += 1

#         # Phase 2 — process saved pages
#         page_files = sorted(
#             f for f in os.listdir(pages_dir)
#             if f.startswith("page_") and f.endswith(".json") and "_ERROR" not in f
#         )

#         if not page_files:
#             logger.info("[APICAR] No pages to process.")
#             return {"message": "No pages processed", "run_dir": run_dir}

#         for fname in page_files:
#             fpath = os.path.join(pages_dir, fname)
#             try:
#                 with open(fpath, "r", encoding="utf-8") as fh:
#                     page_payload = json.load(fh)
#             except Exception as e:
#                 logger.error(f"[FORWARD] Failed to read {fpath}: {e}")
#                 continue

#             data = page_payload.get("data", [])
#             if not data:
#                 continue

#             processed_vehicles = []
#             for vehicle in data:
#                 try:
#                     formatted = format_car_data(vehicle)
#                     adapted = {
#                         "vin": formatted["vin"],
#                         "vehicle": formatted["vehicle"],
#                         "make": formatted["make"],
#                         "model": formatted["model"],
#                         "year": formatted.get("year"),
#                         "mileage": formatted.get("mileage"),
#                         "auction": formatted.get("auction"),
#                         "auction_name": formatted.get("auction_name"),
#                         "date": formatted.get("date").isoformat() if formatted.get("date") else None,
#                         "lot": formatted.get("lot"),
#                         "seller": formatted.get("seller"),
#                         "seller_type": formatted.get("seller_type"),
#                         "location": formatted.get("location"),
#                         "current_bid": formatted.get("current_bid"),
#                         "engine": formatted.get("engine"),
#                         "has_keys": formatted.get("has_keys"),
#                         "engine_title": formatted.get("engine_title"),
#                         "engine_cylinder": formatted.get("engine_cylinder"),
#                         "drive_type": formatted.get("drive_type"),
#                         "exterior_color": formatted.get("exterior_color"),
#                         "condition": formatted.get("condition"),
#                         "body_style": formatted.get("body_style"),
#                         "fuel_type": formatted.get("fuel_type"),
#                         "transmision": formatted.get("transmision"),
#                         "vehicle_type": formatted.get("vehicle_type"),
#                         "link": formatted.get("link"),
#                         "is_salvage": formatted.get("is_salvage", False),
#                         "photos": formatted.get("photos", []),
#                         "photos_hd": formatted.get("photos_hd", []),
#                         "condition_assessments": formatted.get("condition_assessments", []),
#                     }
#                     processed_vehicles.append(adapted)
#                 except Exception:
#                     continue

#             if not processed_vehicles:
#                 continue

#             for batch in chunked(processed_vehicles, 25):
#                 payload = {
#                     "ivent": "created" if base_url else "updated",
#                     "vehicles": batch,
#                 }
#                 try:
#                     r = httpx.post(save_url, json=payload, headers=headers_entities, timeout=3600)
#                     r.raise_for_status()
#                     logger.info(f"[FORWARD] Sent {len(batch)} vehicles from {fname}.")
#                 except httpx.HTTPError as e:
#                     logger.error(f"[FORWARD] Failed batch from {fname}: {e}")

#         logger.info(f"[DONE] All pages processed successfully. Cleaning up {run_dir}...")

#         # Phase 3 — cleanup
#         try:
#             shutil.rmtree(run_dir)
#             logger.info(f"[CLEANUP] Successfully removed {run_dir}")
#         except Exception as e:
#             logger.warning(f"[CLEANUP] Failed to remove {run_dir}: {e}")

#         return {"message": "Finished and cleaned up", "run_dir": run_dir}

#     except Exception as e:
#         logger.error(f"[FATAL] Unexpected error: {e}")
#         return {"error": str(e), "run_dir": run_dir}


# @app.task
# def delete_vehicle():
#     """Delete outdated vehicles."""
#     headers = {"X-Auth-Token": os.getenv("PARSERS_AUTH_TOKEN")}
#     url = "https://api.apicar.store/api/cars/deleted"
#     response = httpx.get(url, timeout=1000, headers={"api-key": os.getenv("APICAR_KEY")})
#     to_delete = response.json()
#     delete_url = "http://entities:8000/api/v1/vehicles/bulk/delete"
#     httpx.post(delete_url, json=to_delete, headers=headers)
