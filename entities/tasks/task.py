import asyncio

# if os.environ.get("CELERY_GEVENT", "0") == "1":
#     from gevent import monkey
#     monkey.patch_all()
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import anyio
import httpx
import redis
import requests
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from core.celery_config import app
from core.config import settings
from db.sync_session import SessionLocal
from integrations.copart_fees import ParsingCopartFeeSource
from integrations.current_bids import ParsingCurrentBidSource
from integrations.parsing import SyncParsingClient
from integrations.vehicle_tasks import CeleryFilterKickoffPublisher
from models.admin import FilterModel, ROIModel
from models.filter_kickoff_queue import (
    FilterKickoffQueueModel,
    FilterKickoffQueueStatus,
)
from models.user import user_likes
from models.vehicle import (
    AutoCheckModel,
    CarInventoryInvestmentsModel,
    CarInventoryModel,
    CarModel,
    CarSaleHistoryModel,
    CarStatus,
    ConditionAssessmentModel,
    FeeModel,
    HistoryModel,
    PartModel,
    PhotoModel,
    RecommendationStatus,
    RelevanceStatus,
)
from schemas.vehicle import CarCreateSchema
from services.email_sync import send_email_sync
from services.fee_import import refresh_copart_fees
from services.filter_kickoff_queue import dispatch_due_filter_kickoffs
from services.lock import (
    acquire_kickoff_lock,
    generate_lock_token,
    is_kickoff_busy,
    release_kickoff_lock,
)
from services.vehicle_bids import refresh_vehicle_bids
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


def is_lock_conflict_error(exc: Exception) -> bool:
    text = str(exc).lower()

    markers = (
        "could not obtain lock on row",
        "lock timeout",
        "canceling statement due to lock timeout",
        "could not obtain lock",
        "nowait",
    )
    return any(marker in text for marker in markers)


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
def _load_default_roi(db: Session) -> Optional[ROIModel]:
    return db.execute(select(ROIModel).order_by(ROIModel.created_at.desc()).limit(1)).scalars().first()


def _load_fees(db: Session, auction: Optional[str], investment: float) -> List[FeeModel]:
    return db.execute(
        select(FeeModel).where(
            FeeModel.auction == auction,
            FeeModel.price_from <= investment,
            FeeModel.price_to >= investment,
        )
    ).scalars().all()


def _apply_fees(investment: float, fees: List[FeeModel]) -> float:
    fee_total = 0.0
    for fee in fees:
        if fee.percent:
            fee_total += (fee.amount / 100.0) * investment
        else:
            fee_total += float(fee.amount)
    return fee_total


def normalize_mileage(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace(",", "").strip()
        if not v:
            return None
    try:
        return int(v)
    except Exception:
        return None


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

    logger.info("parse_and_update_car started for VIN=%s", vin)

    # ---------------------------------------------------------
    # helpers
    # ---------------------------------------------------------
    def add_reason_text(current: Optional[str], text: str) -> str:
        if not text:
            return current or ""
        reason = text if text.endswith(";") else f"{text};"
        current = current or ""
        if reason not in current:
            current += reason
        return current

    def remove_reason_text(current: Optional[str], text: str) -> str:
        if not text or not current:
            return current or ""
        reason = text if text.endswith(";") else f"{text};"
        current = current.replace(reason, "")
        return current.strip()

    def parse_int_safe(value: Any) -> Optional[int]:
        if value is None:
            return None

        if isinstance(value, bool):
            return int(value)

        if isinstance(value, int):
            return value

        if isinstance(value, float):
            return int(value)

        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            cleaned = cleaned.replace(",", "").replace(" ", "")
            match = re.search(r"-?\d+", cleaned)
            if match:
                try:
                    return int(match.group(0))
                except Exception:
                    return None

        return None

    def normalize_owners(value: Any) -> Optional[int]:
        parsed = parse_int_safe(value)
        if parsed is None:
            return None
        return parsed if parsed >= 0 else None

    def normalize_accident_count(value: Any) -> Optional[int]:
        parsed = parse_int_safe(value)
        if parsed is None:
            return None
        return parsed if parsed >= 0 else None

    def normalize_mileage_value(value: Any) -> Optional[int]:
        try:
            normalized = normalize_mileage(value)
            if normalized is None:
                return None
            return int(normalized)
        except Exception:
            return None

    def build_parse_url() -> str:
        return (
            "/api/v1/parsers/scrape/dc"
            f"?car_vin={vin}"
            f"&car_mileage={mileage if mileage is not None else ''}"
            f"&car_name={car_name or ''}"
            f"&car_engine={car_engine or ''}"
            f"&car_make={car_make or ''}"
            f"&car_model={car_model or ''}"
            f"&car_year={car_year if car_year is not None else ''}"
            f"&car_transmison={car_transmison or ''}"
            f"&only_history=false"
        )

    # ---------------------------------------------------------
    # 1. Read current car state WITHOUT LOCK
    # ---------------------------------------------------------
    with SessionLocal() as db:
        car_pre = db.execute(
            select(CarModel).where(CarModel.vin == vin).limit(1)
        ).scalars().first()

        if not car_pre:
            raise ValueError(f"Car with VIN {vin} not found")

        business_attempts = car_pre.attempts or 0
        current_car_id = car_pre.id

    # ---------------------------------------------------------
    # 2. Fetch sales history WITHOUT LOCK
    # ---------------------------------------------------------
    sale_history_rows = []
    sales_history_reason_to_add = None

    if business_attempts == 0:
        try:
            hist_url = f"/api/v1/apicar/get/{vin}"
            headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
            hist_resp = http_get_with_retries(hist_url, headers=headers, timeout=30.0)
            hist_resp.raise_for_status()

            hist_json = hist_resp.json()
            if hist_json is None:
                logger.warning("Sales history response is None for vin=%s", vin)
                sale_history_data = []
            else:
                hist_result = CarCreateSchema.model_validate(hist_json)
                sale_history_data = hist_result.sales_history or []

            logger.info(
                "Sales history fetched for VIN=%s, items=%s",
                vin,
                len(sale_history_data),
            )

            if sale_history_data:
                if len(sale_history_data) >= 4:
                    sales_history_reason_to_add = (
                        f"sales at auction in the last 3 years: {len(sale_history_data)};"
                    )

                for h in sale_history_data:
                    history_payload = h.model_dump() if hasattr(h, "model_dump") else h.dict()
                    if not history_payload.get("source"):
                        history_payload["source"] = "Unknown"
                    sale_history_rows.append(history_payload)

        except Exception as e:
            logger.warning("Sales history fetch failed for %s: %s", vin, e)

    # ---------------------------------------------------------
    # 3. Main parser call WITHOUT LOCK
    # ---------------------------------------------------------
    parse_url = build_parse_url()
    headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}

    try:
        resp = http_get_with_retries(parse_url, headers=headers, timeout=300.0)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("HTTP parser error for VIN=%s: %s", vin, e)

        with SessionLocal() as db:
            try:
                db.execute(text("SET LOCAL lock_timeout = '2s'"))
                car = db.execute(
                    select(CarModel)
                    .where(CarModel.vin == vin)
                    .with_for_update(nowait=True)
                ).scalars().first()

                if not car:
                    return {"status": "not_found", "vin": vin}

                car.recommendation_status_reasons = add_reason_text(
                    car.recommendation_status_reasons,
                    "upstream parser error;"
                )
                car.has_correct_vin = False
                car.attempts = (car.attempts or 0) + 1

                db.add(car)
                db.commit()
                return {"status": "exception", "vin": vin}

            except OperationalError as lock_exc:
                db.rollback()
                logger.warning("VIN=%s is locked, skip after parser HTTP error: %s", vin, lock_exc)
                return {"status": "locked_skip", "vin": vin}
            except Exception:
                db.rollback()
                raise

    try:
        data = resp.json()
    except Exception as e:
        logger.warning("JSON decode failed for VIN=%s: %s", vin, e)

        with SessionLocal() as db:
            try:
                db.execute(text("SET LOCAL lock_timeout = '2s'"))
                car = db.execute(
                    select(CarModel)
                    .where(CarModel.vin == vin)
                    .with_for_update(nowait=True)
                ).scalars().first()

                if not car:
                    return {"status": "not_found", "vin": vin}

                car.recommendation_status_reasons = add_reason_text(
                    car.recommendation_status_reasons,
                    "invalid parser json response;"
                )
                car.has_correct_vin = False
                car.attempts = (car.attempts or 0) + 1

                db.add(car)
                db.commit()
                return {"status": "exception", "vin": vin}

            except OperationalError as lock_exc:
                db.rollback()
                logger.warning("VIN=%s is locked, skip after JSON error: %s", vin, lock_exc)
                return {"status": "locked_skip", "vin": vin}
            except Exception:
                db.rollback()
                raise

    if data.get("error"):
        error_text = str(data.get("error") or "").strip()
        logger.warning("Payload parser error for VIN=%s: %s", vin, error_text)

        with SessionLocal() as db:
            try:
                db.execute(text("SET LOCAL lock_timeout = '2s'"))
                car = db.execute(
                    select(CarModel)
                    .where(CarModel.vin == vin)
                    .with_for_update(nowait=True)
                ).scalars().first()

                if not car:
                    return {"status": "not_found", "vin": vin}

                car.recommendation_status_reasons = add_reason_text(
                    car.recommendation_status_reasons,
                    f"scraping error: {error_text};"
                )
                car.has_correct_vin = False
                car.attempts = (car.attempts or 0) + 1

                db.add(car)
                db.commit()
                return {"status": "exception", "vin": vin}

            except OperationalError as lock_exc:
                db.rollback()
                logger.warning("VIN=%s is locked, skip after payload error: %s", vin, lock_exc)
                return {"status": "locked_skip", "vin": vin}
            except Exception:
                db.rollback()
                raise

    # ---------------------------------------------------------
    # 4. Prepare all computed values WITHOUT LOCK
    # ---------------------------------------------------------
    parsed_owners = normalize_owners(data.get("owners"))
    parsed_mileage = normalize_mileage_value(data.get("mileage"))
    parsed_accident_count = normalize_accident_count(data.get("accident_count"))

    price_values_by_source = {}
    for key in ("jd", "d_max", "manheim"):
        parsed_price = parse_int_safe(data.get(key))
        if parsed_price is not None and parsed_price > 0:
            price_values_by_source[key] = parsed_price

    from services.market_price import merge_market_prices

    previous_sources = car_pre.market_price_sources
    candidate_avg_market_price, merged_sources = merge_market_prices(
        car_pre.avg_market_price, previous_sources, price_values_by_source,
    )
    market_refresh_is_complete = len(price_values_by_source) == 3
    previous_avg_market_price = car_pre.avg_market_price
    should_refresh_market = candidate_avg_market_price is not None and (
        candidate_avg_market_price != previous_avg_market_price
    )
    calculation_market_price = (
        candidate_avg_market_price
        if should_refresh_market
        else previous_avg_market_price
    )

    with SessionLocal() as db:
        default_roi = _load_default_roi(db)

        if default_roi and calculation_market_price:
            predicted_total_investments = calculation_market_price / (1 + default_roi.roi / 100.0)
            predicted_profit_margin_percent = default_roi.profit_margin
            predicted_profit_margin = calculation_market_price * (default_roi.profit_margin / 100.0)
            predicted_roi = default_roi.roi
        else:
            predicted_total_investments = 0.0
            predicted_profit_margin_percent = 0.0
            predicted_profit_margin = 0.0
            predicted_roi = 0.0

        current_auction = car_pre.auction
        fees = _load_fees(db, current_auction, float(predicted_total_investments or 0.0))
        auction_fee = _apply_fees(float(predicted_total_investments or 0.0), fees)

    # ---------------------------------------------------------
    # 5. Upload HTML to S3 WITHOUT LOCK
    # ---------------------------------------------------------
    screenshot_url = None
    html_data = data.get("html_data")

    if html_data:
        try:
            s3_storage = S3StorageClient(
                endpoint_url=settings.S3_STORAGE_ENDPOINT,
                access_key=settings.S3_STORAGE_ACCESS_KEY,
                secret_key=settings.S3_STORAGE_SECRET_KEY,
                bucket_name=settings.S3_BUCKET_NAME,
            )
            file_key = (
                f"auto_checks/{vin}/"
                f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_report.html"
            )
            s3_storage.upload_fileobj_sync(
                file_key,
                BytesIO(html_data.encode("utf-8")),
            )
            screenshot_url = (
                f"{settings.S3_STORAGE_ENDPOINT}/"
                f"{settings.S3_BUCKET_NAME}/"
                f"{file_key}"
            )
        except Exception as e:
            logger.warning("S3 upload failed for VIN=%s: %s", vin, e)

    # ---------------------------------------------------------
    # 6. Final SHORT locked DB write
    # ---------------------------------------------------------
    with SessionLocal() as db:
        try:
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            db.execute(text("SET LOCAL statement_timeout = '15s'"))

            car = db.execute(
                select(CarModel)
                .where(CarModel.vin == vin)
                .options(selectinload(CarModel.condition_assessments))
                .with_for_update(nowait=True)
            ).scalars().first()

            if not car:
                raise ValueError(f"Car with VIN {vin} not found")

            # if sales history still absent and we fetched it on first business attempt
            if business_attempts == 0 and sale_history_rows and not car.sales_history:
                if len(sale_history_rows) >= 4:
                    car.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                    car.recommendation_manually_set = False
                    car.recommendation_status_reasons = add_reason_text(
                        car.recommendation_status_reasons,
                        sales_history_reason_to_add or ""
                    )

                for row in sale_history_rows:
                    item = CarSaleHistoryModel(**row, car_id=car.id)
                    db.add(item)

            # reset parser-related errors
            car.has_correct_vin = True
            car.recommendation_status_reasons = remove_reason_text(
                car.recommendation_status_reasons,
                "upstream parser error;"
            )
            car.recommendation_status_reasons = remove_reason_text(
                car.recommendation_status_reasons,
                "invalid parser json response;"
            )

            # owners
            if parsed_owners is not None:
                car.owners = parsed_owners
                car.has_correct_owners = True
            else:
                car.has_correct_owners = False

            # mileage
            expected_mileage = mileage if mileage is not None else car.mileage
            if parsed_mileage is not None and expected_mileage is not None:
                try:
                    car.has_correct_mileage = int(expected_mileage) == int(parsed_mileage)
                    if car.has_correct_mileage:
                        car.recommendation_status_reasons = remove_reason_text(
                            car.recommendation_status_reasons,
                            "mileage mismatch;"
                        )
                    else:
                        car.recommendation_status_reasons = add_reason_text(
                            car.recommendation_status_reasons,
                            f"mileage mismatch: expected {expected_mileage}, parsed {parsed_mileage};"
                        )
                except Exception:
                    car.has_correct_mileage = False
            else:
                car.has_correct_mileage = False

            # accidents
            if parsed_accident_count is not None:
                car.accident_count = parsed_accident_count
                car.has_correct_accidents = True
            else:
                car.has_correct_accidents = False

            # Market-derived fields are one consistency unit. Do not replace an
            # established price with a partial/empty upstream response, and do
            # not overwrite a manual price saved while this task was running.
            market_snapshot_is_current = (
                car.avg_market_price == previous_avg_market_price
                and car.market_price_sources == previous_sources
            )
            if market_snapshot_is_current and merged_sources:
                car.market_price_sources = merged_sources
            if should_refresh_market and market_snapshot_is_current:
                car.avg_market_price = candidate_avg_market_price
                car.predicted_total_investments = predicted_total_investments
                car.predicted_profit_margin_percent = predicted_profit_margin_percent
                car.predicted_profit_margin = predicted_profit_margin
                car.auction_fee = auction_fee
                car.predicted_roi = predicted_roi
                car.suggested_bid = int(
                    (car.predicted_total_investments or 0.0) - (car.sum_of_investments or 0.0)
                )
            elif candidate_avg_market_price is None:
                logger.warning(
                    "VIN=%s returned no valid market prices; keeping established market fields",
                    vin,
                )
            elif not market_refresh_is_complete and previous_avg_market_price is not None:
                logger.warning(
                    "VIN=%s returned partial market data (%s); keeping established market fields",
                    vin,
                    sorted(price_values_by_source),
                )
            elif not market_snapshot_is_current:
                logger.info(
                    "VIN=%s market price changed concurrently; parser result was not applied",
                    vin,
                )

            # autochek html
            if screenshot_url:
                db.add(AutoCheckModel(car_id=car.id, screenshot_url=screenshot_url))

            # recommendation baseline
            if (
                car.recommendation_status != RecommendationStatus.NOT_RECOMMENDED
                and (not car.recommendation_status_reasons or car.recommendation_status_reasons == "")
            ):
                car.recommendation_status = RecommendationStatus.RECOMMENDED

            car.is_checked = True
            car.relevance = RelevanceStatus.ACTIVE
            car.attempts = (car.attempts or 0) + 1

            db.add(car)
            db.commit()

            logger.info(
                "parse_and_update_car: updated VIN=%s | owners=%s mileage=%s accidents=%s attempts=%s",
                vin,
                car.has_correct_owners,
                car.has_correct_mileage,
                car.has_correct_accidents,
                car.attempts,
            )
            return {"status": "success", "vin": vin}

        except OperationalError as e:
            db.rollback()

            if is_lock_conflict_error(e):
                try:
                    retry_on_lock(self, e, vin)
                except MaxRetriesExceededError:
                    logger.warning("Max lock retries exceeded for VIN=%s", vin)
                    return {"status": "locked_skip_after_retries", "vin": vin}

            logger.error("OperationalError for VIN=%s: %s", vin, e, exc_info=True)
            raise
        except Exception as e:
            db.rollback()
            logger.error("parse_and_update_car failed for VIN %s: %s", vin, e, exc_info=True)
            raise


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
    queue_item_id = int(lock_token)

    try:
        with SessionLocal() as session:
            filt = session.get(FilterModel, filter_id)
            if not filt:
                queue_item = session.get(FilterKickoffQueueModel, queue_item_id)
                if queue_item:
                    queue_item.status = FilterKickoffQueueStatus.FAILED
                    queue_item.finished_at = datetime.now(timezone.utc)
                    queue_item.last_error = "filter_not_found"
                    session.commit()

                return {
                    "status": "error",
                    "reason": "filter_not_found",
                    "filter_id": filter_id,
                }

            conditions = [
                CarModel.make == filt.make,
                CarModel.year >= (filt.year_from or 0),
                CarModel.year <= (filt.year_to or 3000),
                CarModel.mileage >= (filt.odometer_min or 0),
                CarModel.mileage <= (filt.odometer_max or 10_000_000),
                CarModel.avg_market_price.is_(None),
                CarModel.is_checked.is_(False),
                CarModel.attempts < 3,
            ]

            if filt.model is not None:
                conditions.append(CarModel.model == filt.model)

            stmt = (
                select(
                    CarModel.vin,
                    CarModel.vehicle,
                    CarModel.engine_title,
                    CarModel.mileage,
                    CarModel.make,
                    CarModel.model,
                    CarModel.year,
                    CarModel.transmision,
                )
                .where(and_(*conditions))
            )

            result = session.execute(stmt).mappings().all()

        batch = []
        enqueued = 0

        for row in result:
            batch.append(
                {
                    "vin": row["vin"],
                    "vehicle": row["vehicle"],
                    "engine_title": row["engine_title"],
                    "mileage": row["mileage"],
                    "make": row["make"],
                    "model": row["model"],
                    "year": row["year"],
                    "transmision": row["transmision"],
                }
            )

            if len(batch) >= batch_size:
                for v in batch:
                    parse_and_update_car.delay(
                        vin=v["vin"],
                        car_name=v["vehicle"],
                        car_engine=v["engine_title"],
                        mileage=v["mileage"],
                        car_make=v["make"],
                        car_model=v["model"],
                        car_year=v["year"],
                        car_transmison=v["transmision"],
                    )

                enqueued += len(batch)
                batch.clear()

        if batch:
            for v in batch:
                parse_and_update_car.delay(
                    vin=v["vin"],
                    car_name=v["vehicle"],
                    car_engine=v["engine_title"],
                    mileage=v["mileage"],
                    car_make=v["make"],
                    car_model=v["model"],
                    car_year=v["year"],
                    car_transmison=v["transmision"],
                )

            enqueued += len(batch)

        with SessionLocal() as session:
            queue_item = session.get(FilterKickoffQueueModel, queue_item_id)
            if queue_item:
                queue_item.status = FilterKickoffQueueStatus.DONE
                queue_item.finished_at = datetime.now(timezone.utc)
                queue_item.last_error = None
                session.commit()

        return {
            "status": "ok",
            "filter_id": filter_id,
            "enqueued": enqueued,
        }

    except Exception as exc:
        with SessionLocal() as session:
            queue_item = session.get(FilterKickoffQueueModel, queue_item_id)
            if queue_item:
                queue_item.status = FilterKickoffQueueStatus.FAILED
                queue_item.finished_at = datetime.now(timezone.utc)
                queue_item.last_error = str(exc)
                session.commit()
        raise


@app.task(name="tasks.task.parse_and_update_cars_with_expired_auction_date")
def parse_and_update_cars_with_expired_auction_date() -> Dict[str, Any]:
    """
    Flow:
    1. Hard-delete IRRELEVANT cars with past date.
    2. For ACTIVE cars with past date:
       - call parser API
       - 404 -> ARCHIVAL
       - 200 -> update existing row
         - if refreshed date is still in the past (or None) and not BuyNow -> ARCHIVAL
         - else -> ACTIVE
    """

    now_utc = datetime.now(timezone.utc)

    stats = {
        "checked": 0,
        "archived": 0,
        "reactivated_or_kept_active": 0,
        "deleted_irrelevant": 0,
        "updated": 0,
        "errors": 0,
    }

    def _add_reason_if_missing(car: CarModel, reason: str) -> None:
        if not reason:
            return
        if not reason.endswith(";"):
            reason = f"{reason};"
        current = car.recommendation_status_reasons or ""
        if reason not in current:
            car.recommendation_status_reasons = f"{current}{reason}"

    def _apply_update_from_schema(
        db: Session,
        existing_vehicle: CarModel,
        vehicle_info: CarCreateSchema,
    ) -> None:
        preserve_manual_recommendation = bool(
            existing_vehicle.recommendation_manually_set
        )
        previous_recommendation = existing_vehicle.recommendation_status
        previous_reasons = existing_vehicle.recommendation_status_reasons
        previous_fuel_type = existing_vehicle.fuel_type
        previous_transmission = existing_vehicle.transmision
        previous_current_bid = existing_vehicle.current_bid
        previous_damage_issues = {
            assessment.issue_description
            for assessment in existing_vehicle.condition_assessments
            if assessment.issue_description
        }
        recommendation_override_triggered = False

        # 1) scalar fields
        for field, value in vehicle_info.dict(
            exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}
        ).items():
            if value is not None or field == "date":
                setattr(existing_vehicle, field, value)

                if field == "fuel_type" and value not in ["Gasoline", "Flexible Fuel", "Unknown"]:
                    if not preserve_manual_recommendation or value != previous_fuel_type:
                        existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        _add_reason_if_missing(existing_vehicle, str(value))
                        recommendation_override_triggered = True

                if field == "transmision" and value != "Automatic":
                    if not preserve_manual_recommendation or value != previous_transmission:
                        existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        _add_reason_if_missing(existing_vehicle, str(value))
                        recommendation_override_triggered = True

        # 2) photos — add only missing
        existing_photo_urls = {p.url for p in existing_vehicle.photos}
        new_photos = []

        if vehicle_info.photos:
            for p in vehicle_info.photos:
                if p.url not in existing_photo_urls:
                    new_photos.append(
                        PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=False)
                    )

        if vehicle_info.photos_hd:
            for p in vehicle_info.photos_hd:
                if p.url not in existing_photo_urls:
                    new_photos.append(
                        PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=True)
                    )

        if new_photos:
            db.add_all(new_photos)

        # 3) condition assessments — full replace
        db.execute(
            delete(ConditionAssessmentModel).where(
                ConditionAssessmentModel.car_id == existing_vehicle.id
            )
        )

        if vehicle_info.condition_assessments:
            bad_issues = {
                "Rejected Repair",
                "Burn Engine",
                "Mechanical",
                "Replaced Vin",
                "Burn",
                "Undercarriage",
                "Water/Flood",
                "Burn Interior",
                "Rollover",
            }

            for a in vehicle_info.condition_assessments:
                db.add(
                    ConditionAssessmentModel(
                        type_of_damage=a.type_of_damage,
                        issue_description=a.issue_description,
                        car_id=existing_vehicle.id,
                    )
                )
                if a.issue_description in bad_issues:
                    if (
                        not preserve_manual_recommendation
                        or a.issue_description not in previous_damage_issues
                    ):
                        existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        _add_reason_if_missing(existing_vehicle, a.issue_description)
                        recommendation_override_triggered = True

        # 4) current bid > suggested bid
        if (
            vehicle_info.current_bid is not None
            and existing_vehicle.suggested_bid is not None
            and vehicle_info.current_bid > existing_vehicle.suggested_bid
            and (
                not preserve_manual_recommendation
                or vehicle_info.current_bid != previous_current_bid
            )
        ):
            existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
            _add_reason_if_missing(existing_vehicle, "suggested bid < current bid")
            recommendation_override_triggered = True

        # 5) sales history — add only if not present in DB
        if not existing_vehicle.sales_history and vehicle_info.sales_history:
            if len(vehicle_info.sales_history) >= 4:
                existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                recommendation_override_triggered = True
                _add_reason_if_missing(
                    existing_vehicle,
                    f"sales at auction in the last 3 years: {len(vehicle_info.sales_history)}",
                )

            history_rows = []
            for h in vehicle_info.sales_history:
                item = CarSaleHistoryModel(**h.dict(), car_id=existing_vehicle.id)
                if not item.source:
                    item.source = "Unknown"
                history_rows.append(item)

            if history_rows:
                db.add_all(history_rows)

        if preserve_manual_recommendation and not recommendation_override_triggered:
            existing_vehicle.recommendation_status = previous_recommendation
            existing_vehicle.recommendation_status_reasons = previous_reasons
        elif recommendation_override_triggered:
            existing_vehicle.recommendation_manually_set = False

        db.add(existing_vehicle)

    # -----------------------------------
    # 1. Hard delete IRRELEVANT past cars
    # -----------------------------------
    with SessionLocal() as db:
        try:
            irrelevant_ids = db.execute(
                select(CarModel.id).where(
                    and_(
                        CarModel.relevance == RelevanceStatus.IRRELEVANT,
                        CarModel.car_status != CarStatus.WON,
                        CarModel.date.is_not(None),
                        CarModel.date <= now_utc,
                    )
                )
            ).scalars().all()

            if irrelevant_ids:
                # 1) Знайти inventory, прив'язані до цих авто
                inventory_ids = db.execute(
                    select(CarInventoryModel.id).where(
                        CarInventoryModel.car_id.in_(irrelevant_ids)
                    )
                ).scalars().all()

                # 2) Видалити залежності від car_inventory
                if inventory_ids:
                    db.execute(
                        delete(CarInventoryInvestmentsModel).where(
                            CarInventoryInvestmentsModel.car_inventory_id.in_(inventory_ids)
                        )
                    )

                    db.execute(
                        delete(HistoryModel).where(
                            HistoryModel.car_inventory_id.in_(inventory_ids)
                        )
                    )

                    db.execute(
                        delete(CarInventoryModel).where(
                            CarInventoryModel.id.in_(inventory_ids)
                        )
                    )

                # 3) Видалити залежності напряму від cars
                db.execute(
                    delete(user_likes).where(
                        user_likes.c.car_id.in_(irrelevant_ids)
                    )
                )

                db.execute(
                    delete(AutoCheckModel).where(
                        AutoCheckModel.car_id.in_(irrelevant_ids)
                    )
                )

                db.execute(
                    delete(PhotoModel).where(
                        PhotoModel.car_id.in_(irrelevant_ids)
                    )
                )

                db.execute(
                    delete(ConditionAssessmentModel).where(
                        ConditionAssessmentModel.car_id.in_(irrelevant_ids)
                    )
                )

                db.execute(
                    delete(CarSaleHistoryModel).where(
                        CarSaleHistoryModel.car_id.in_(irrelevant_ids)
                    )
                )

                db.execute(
                    delete(PartModel).where(
                        PartModel.car_id.in_(irrelevant_ids)
                    )
                )

                db.execute(
                    delete(HistoryModel).where(
                        HistoryModel.car_id.in_(irrelevant_ids)
                    )
                )

                # 4) І тільки після цього видалити самі авто
                db.execute(
                    delete(CarModel).where(
                        CarModel.id.in_(irrelevant_ids)
                    )
                )

                stats["deleted_irrelevant"] = len(irrelevant_ids)
                db.commit()

            logger.info(
                "Deleted IRRELEVANT expired cars: %s",
                stats["deleted_irrelevant"],
            )

        except Exception:
            db.rollback()
            logger.exception("Failed to delete irrelevant expired cars")
            stats["errors"] += 1

    # -----------------------------------
    # 2. Process ACTIVE past cars
    # -----------------------------------
    with SessionLocal() as db:
        vin_rows = db.execute(
            select(CarModel.vin).where(
                and_(
                    CarModel.relevance == RelevanceStatus.ACTIVE,
                    CarModel.car_status != CarStatus.WON,
                    CarModel.date.is_not(None),
                    CarModel.date <= now_utc,
                )
            )
        ).scalars().all()

        logger.info("Expired-auction: selected %d ACTIVE VINs to process", len(vin_rows))

        for vin in vin_rows:
            stats["checked"] += 1

            try:
                resp = SyncParsingClient(timeout=30).get(
                    f"/api/v1/apicar/{vin}",
                    timeout=30.0,
                )
            except Exception as e:
                logger.exception("VIN %s: HTTP request failed: %s", vin, e)
                stats["errors"] += 1
                continue

            if resp.status_code == 404:
                try:
                    car = db.execute(
                        select(CarModel).where(CarModel.vin == vin)
                    ).scalars().first()

                    if car:
                        car.relevance = RelevanceStatus.ARCHIVAL
                        car.is_manually_upserted = False
                        db.add(car)
                        db.commit()
                        stats["archived"] += 1
                        logger.info("VIN %s marked as ARCHIVAL after 404", vin)
                except Exception as e:
                    db.rollback()
                    logger.exception("VIN %s: failed to mark ARCHIVAL: %s", vin, e)
                    stats["errors"] += 1

                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.exception("VIN %s: parser returned HTTP error: %s", vin, e)
                stats["errors"] += 1
                continue

            try:
                payload = resp.json()
                vehicle_info = CarCreateSchema.model_validate(payload)
            except Exception as e:
                logger.exception("VIN %s: payload validation failed: %s", vin, e)
                stats["errors"] += 1
                continue

            try:
                existing_vehicle = db.execute(
                    select(CarModel)
                    .options(
                        selectinload(CarModel.photos),
                        selectinload(CarModel.sales_history),
                        selectinload(CarModel.condition_assessments),
                    )
                    .where(CarModel.vin == vin)
                ).scalars().first()

                if not existing_vehicle:
                    logger.info("VIN %s: not found during update stage", vin)
                    continue

                _apply_update_from_schema(db, existing_vehicle, vehicle_info)

                is_buynow = (
                    existing_vehicle.auction_name is not None
                    and existing_vehicle.auction_name.lower() == "buynow"
                )

                # final relevance decision after refresh
                if not is_buynow and (
                    existing_vehicle.date is None or existing_vehicle.date <= now_utc
                ):
                    existing_vehicle.relevance = RelevanceStatus.ARCHIVAL
                    existing_vehicle.is_manually_upserted = False
                    stats["archived"] += 1
                    logger.info("VIN %s archived after refresh", vin)
                else:
                    existing_vehicle.relevance = RelevanceStatus.ACTIVE
                    stats["reactivated_or_kept_active"] += 1
                    logger.info("VIN %s kept ACTIVE after refresh", vin)

                db.add(existing_vehicle)
                db.commit()
                stats["updated"] += 1

            except IntegrityError as e:
                db.rollback()
                logger.exception("VIN %s: IntegrityError: %s", vin, getattr(e, "orig", e))
                stats["errors"] += 1
            except Exception as e:
                db.rollback()
                logger.exception("VIN %s: update failed: %s", vin, e)
                stats["errors"] += 1

    logger.info("Expired auction update finished: %s", stats)
    return stats

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
