import asyncio

# if os.environ.get("CELERY_GEVENT", "0") == "1":
#     from gevent import monkey
#     monkey.patch_all()
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import anyio
import httpx
import redis
import requests
from celery.exceptions import MaxRetriesExceededError

from core.celery_config import app
from core.config import settings
from db.sync_session import SessionLocal
from exceptions.vehicle_enrichment import EnrichmentLockConflict
from integrations.copart_fees import ParsingCopartFeeSource
from integrations.current_bids import ParsingCurrentBidSource
from integrations.expired_vehicles import ParsingExpiredVehicleSource
from integrations.parsing import SyncParsingClient
from integrations.vehicle_enrichment import ParsingVehicleEnrichmentSource, S3EnrichmentReportStorage
from integrations.vehicle_tasks import CeleryFilterKickoffPublisher, CeleryTaskVehicleEnrichmentPublisher
from services.email_sync import send_email_sync
from services.expired_vehicle_refresh import refresh_expired_vehicles
from services.fee_import import refresh_copart_fees
from services.filter_execution import execute_filter_kickoff
from services.filter_kickoff_queue import dispatch_due_filter_kickoffs
from services.lock import (
    acquire_kickoff_lock,
    generate_lock_token,
    is_kickoff_busy,
    release_kickoff_lock,
)
from services.vehicle_bids import refresh_vehicle_bids
from services.vehicle_enrichment_workflow import enrich_vehicle
from storages import S3StorageClient

# =========================
# Logging
# =========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Suppress excessive SQLAlchemy logs (INSERT ... VALUES ..., pool, etc.)
for name in (
    "sqlalchemy",
    "sqlalchemy.engine",
    "sqlalchemy.engine.Engine",
    "sqlalchemy.pool",
    "sqlalchemy.dialects",
):
    lg = logging.getLogger(name)
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    lg.handlers.clear()


# =========================
# DB sessions are provided by the shared synchronous database factory.
# =========================


# =========================
# HTTP helpers (sync)
# =========================
def http_get_with_retries(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> httpx.Response:
    delay = 0.5
    last_exc: Optional[Exception] = None
    with SyncParsingClient(timeout=timeout) as client:
        for attempt in range(1, max_retries + 1):
            try:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                return r
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                last_exc = e
                logger.warning(f"HTTP GET attempt {attempt} failed: {e}")
                if attempt == max_retries:
                    break
                time.sleep(delay)
                delay = min(delay * 2, 10.0)
    assert last_exc is not None
    raise last_exc


def get_retry_countdown(retries: int) -> int:
    """
    Exponential backoff:
    1st retry  -> 60s
    2nd retry  -> 120s
    3rd retry  -> 240s
    """
    return min(60 * (2 ** retries), 900)


def is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True

    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in {408, 425, 429, 500, 502, 503, 504}

    return False


def is_temporary_payload_error(error_text: str) -> bool:
    """
    Only for temporary upstream/parser-side failures.
    Business errors must not be retried by Celery.
    """
    text = (error_text or "").strip().lower()

    transient_markers = (
        "Unexpected error during scraping",
    )
    return any(marker in text for marker in transient_markers)




def retry_on_lock(self, exc: Exception, vin: str) -> None:
    countdown = get_retry_countdown(self.request.retries)
    logger.warning(
        "VIN=%s is locked. Retry %s/%s in %ss. Error=%s",
        vin,
        self.request.retries + 1,
        self.max_retries,
        countdown,
        exc,
    )
    raise self.retry(exc=exc, countdown=countdown)


def http_post_with_retries(
    url: str,
    json: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> httpx.Response:
    delay = 0.5
    last_exc: Optional[Exception] = None
    with SyncParsingClient(timeout=timeout) as client:
        for attempt in range(1, max_retries + 1):
            try:
                r = client.post(url, json=json, headers=headers)
                r.raise_for_status()
                return r
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                last_exc = e
                logger.warning(f"HTTP POST attempt {attempt} failed: {e}")
                if attempt == max_retries:
                    break
                time.sleep(delay)
                delay = min(delay * 2, 10.0)
    assert last_exc is not None
    raise last_exc


# =========================
# Small util: run an async function via asyncio.run if it's a coroutine; otherwise — call it as is
# =========================
def run_async(func: Callable[..., Any], *args, **kwargs) -> Union[Any, asyncio.Task]:
    try:
        loop = asyncio.get_running_loop()   # there is an active loop in this thread
    except RuntimeError:
        # no loop is running — start our own
        return anyio.run(func, *args, **kwargs)
    else:
        # loop is already running — schedule the task and RETURN the Task
        return loop.create_task(func(*args, **kwargs))



# =========================
# Core helpers (pure sync)
# =========================


# # =========================
# # Celery Tasks (sync)
# # =========================
@app.task(
    name="tasks.task.parse_and_update_car",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def parse_and_update_car(
    self,
    vin: str,
    car_name: Optional[str] = None,
    car_engine: Optional[str] = None,
    mileage: Optional[int] = None,
    car_make: Optional[str] = None,
    car_model: Optional[str] = None,
    car_year: Optional[int] = None,
    car_transmison: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Lock-optimized synchronous task.

    Strategy:
    1. Read car without lock.
    2. Fetch sales history without lock.
    3. Call parser without lock.
    4. Prepare all calculations without lock.
    5. Upload HTML to S3 without lock.
    6. Lock row only for final DB write using FOR UPDATE NOWAIT.
    """

    request = {
        "vin": vin,
        "car_name": car_name,
        "car_engine": car_engine,
        "mileage": mileage,
        "car_make": car_make,
        "car_model": car_model,
        "car_year": car_year,
        "car_transmison": car_transmison,
    }
    source = ParsingVehicleEnrichmentSource(http_get_with_retries, settings.PARSERS_AUTH_TOKEN)

    def report_storage() -> S3EnrichmentReportStorage:
        return S3EnrichmentReportStorage(
            S3StorageClient,
            endpoint_url=settings.S3_STORAGE_ENDPOINT,
            access_key=settings.S3_STORAGE_ACCESS_KEY,
            secret_key=settings.S3_STORAGE_SECRET_KEY,
            bucket_name=settings.S3_BUCKET_NAME,
            clock=datetime.utcnow,
        )

    try:
        return enrich_vehicle(SessionLocal, request, source, report_storage)
    except EnrichmentLockConflict as exc:
        try:
            retry_on_lock(self, exc.error, vin)
        except MaxRetriesExceededError:
            logger.warning("Max lock retries exceeded for VIN=%s", vin)
            return {"status": "locked_skip_after_retries", "vin": vin}


@app.task(name="tasks.task.update_car_bids")
def update_car_bids() -> Dict[str, Any]:
    logger.info("update_car_bids: start")
    with SessionLocal() as db:
        result = refresh_vehicle_bids(db, ParsingCurrentBidSource(http_post_with_retries, settings.PARSERS_AUTH_TOKEN))
    logger.info("update_car_bids: updated=%s", result["updated_cars"])
    return result


@app.task(name="tasks.task.update_fees")
def update_car_fees() -> Dict[str, Any]:
    logger.info("update_car_fees: start")
    with SessionLocal() as db:
        result = refresh_copart_fees(db, ParsingCopartFeeSource(SyncParsingClient))
    logger.info("update_car_fees: OK")
    return result


@app.task(name="tasks.task.kickoff_parse_for_filter")
def kickoff_parse_for_filter(
    filter_id: int,
    lock_token: str,
    batch_size: int = 100,
    stream_chunk: int = 400,
) -> dict:
    return execute_filter_kickoff(
        SessionLocal, CeleryTaskVehicleEnrichmentPublisher(parse_and_update_car), filter_id, lock_token, batch_size
    )

@app.task(name="tasks.task.parse_and_update_cars_with_expired_auction_date")
def parse_and_update_cars_with_expired_auction_date() -> Dict[str, Any]:
    return refresh_expired_vehicles(
        SessionLocal, ParsingExpiredVehicleSource(SyncParsingClient), datetime.now(timezone.utc)
    )

AUDIT_DIR = Path("audit_logs")

@app.task(name="tasks.task.send_daily_car_audit")
def send_daily_car_audit():

    yesterday = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    file_path = AUDIT_DIR / f"car_updates_{yesterday}.jsonl"

    if not file_path.exists():
        return {"status": "no_file"}

    try:
        send_email_sync(
            to_email=os.getenv("ADMIN_USERNAME"),
            subject=f"Car updates report {yesterday}",
            body="Attached car updates report",
            attachment_path=str(file_path),
        )

        file_path.unlink()

        return {"status": "sent"}

    except Exception as e:
        logger.exception("Send audit failed")
        return {"status": "error", "error": str(e)}


@app.task(name="tasks.task.dispatch_due_filter_kickoff_jobs")
def dispatch_due_filter_kickoff_jobs() -> dict:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        return dispatch_due_filter_kickoffs(session, CeleryFilterKickoffPublisher(app), now)
