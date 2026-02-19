# tasks.py — synchronous Celery tasks for gevent pool
# --------------------------------------------------
import asyncio

# if os.environ.get("CELERY_GEVENT", "0") == "1":
#     from gevent import monkey
#     monkey.patch_all()
import logging
import os
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Union

import anyio
import httpx
import redis
from sqlalchemy import and_, create_engine, delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from core.celery_config import app
from core.config import settings
from db.session import POSTGRESQL_DATABASE_URL
from models.admin import FilterModel, ROIModel
from models.vehicle import (
    AutoCheckModel,
    CarModel,
    CarSaleHistoryModel,
    ConditionAssessmentModel,
    FeeModel,
    PhotoModel,
    RecommendationStatus,
    RelevanceStatus,
)
from schemas.vehicle import CarCreateSchema
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
# DB (sync) — psycopg2
# =========================
if "+asyncpg" in POSTGRESQL_DATABASE_URL:
    SYNC_DB_URL = POSTGRESQL_DATABASE_URL.replace("+asyncpg", "+psycopg2")
else:
    SYNC_DB_URL = POSTGRESQL_DATABASE_URL

ENGINE = create_engine(
    SYNC_DB_URL,
    pool_size=50,
    max_overflow=10,
    pool_timeout=30,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(bind=ENGINE, class_=Session, autoflush=False, autocommit=False, future=True)


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
    with httpx.Client(timeout=timeout) as client:
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


def http_post_with_retries(
    url: str,
    json: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> httpx.Response:
    delay = 0.5
    last_exc: Optional[Exception] = None
    with httpx.Client(timeout=timeout) as client:
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


# # =========================
# # Celery Tasks (sync)
# # =========================
@app.task(name="tasks.task.parse_and_update_car")
def parse_and_update_car(
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
    Fully synchronous task:
    1) (First attempt only) pulls sales history directly and stores it.
    2) Calls the parser (scrape/dc) and updates the vehicle using payload values.
    3) Computes avg market price, investments, margins, fees, suggested bid, ROI.
    4) Stores HTML to S3 (if present).
    5) Baseline: RECOMMENDED only if no reasons collected and status wasn't NOT_RECOMMENDED.
    """
    logger.info(f"parse_and_update_car: VIN={vin}")

    with SessionLocal() as db:
        # -----------------------------------------
        # helpers
        # -----------------------------------------
        def add_reason(_car: CarModel, text: str) -> None:
            if not text:
                return
            reason = text if text.endswith(";") else f"{text};"
            if not _car.recommendation_status_reasons:
                _car.recommendation_status_reasons = reason
            elif reason not in _car.recommendation_status_reasons:
                _car.recommendation_status_reasons += reason

        # прочитаємо car одразу (щоб знати attempts для логіки history)
        car_pre = db.execute(
            select(CarModel).where(CarModel.vin == vin).limit(1)
        ).scalars().first()

        # --------------------------
        # 1) Sales history (FIRST ATTEMPT ONLY)
        # --------------------------
        # тягнемо історію лише якщо це перша спроба (attempts == 0/None)
        if car_pre and ((car_pre.attempts or 0) == 0):
            try:
                hist_url = f"http://parsers:8001/api/v1/apicar/get/{vin}"
                headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
                hist_resp = http_get_with_retries(hist_url, headers=headers, timeout=30.0)
                hist_resp.raise_for_status()
                try:
                    hist_result = CarCreateSchema.model_validate(hist_resp.json())
                    sale_history_data = hist_result.sales_history
                except Exception:
                    sale_history_data = []

                logger.info(f"Successfully scraped sales history data {hist_result.sales_history}")

                # If 4+ sales in last years -> NOT_RECOMMENDED with a reason
                if sale_history_data:
                    if len(sale_history_data) >= 4:
                        logger.debug(
                            "More than 3 sales history records for VIN=%s. Not recommended.",
                            vin,
                        )
                        car_pre.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        add_reason(
                            car_pre,
                            f"sales at auction in the last 3 years: {len(sale_history_data)};"
                        )
                        db.add(car_pre)
                        db.flush()
    
                    # Persist history entries (fill default source if empty)
                    for h in sale_history_data:
                        item = CarSaleHistoryModel(**h.dict(), car_id=car_pre.id)
                        if not item.source:
                            item.source = "Unknown"
                        db.add(item)

                db.commit()
            except Exception as e:
                logger.warning(f"Sales history fetch failed for {vin}: {e}")
        else:
            logger.info(
                "Skipping sales history fetch for VIN=%s (attempts=%s)",
                vin, None if not car_pre else (car_pre.attempts or 0)
            )

        # --------------------------
        # 2) Main parsing + updates
        # --------------------------
        try:
            parse_url = (
                "http://parsers:8001/api/v1/parsers/scrape/dc"
                f"?car_vin={vin}"
                f"&car_mileage={mileage}"
                f"&car_name={car_name}"
                f"&car_engine={car_engine}"
                f"&car_make={car_make}"
                f"&car_model={car_model}"
                f"&car_year={car_year}"
                f"&car_transmison={car_transmison}"
                f"&only_history=false"
            )
            headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
            resp = http_get_with_retries(parse_url, headers=headers, timeout=300.0)
            data = resp.json()

            # lock the car row up-front (so we can safely mutate + count attempts on HTTP error)
            car_q = (
                select(CarModel)
                .where(CarModel.vin == vin)
                .options(selectinload(CarModel.condition_assessments))
                .with_for_update()
            )
            car = db.execute(car_q).scalars().first()
            if not car:
                raise ValueError(f"Car with VIN {vin} not found")

            # HTTP status failure -> mark attempt + vin flag + reason, return exception
            try:
                resp.raise_for_status()
            except Exception as e:
                logger.info(f"Exception: {e} for VIN: {vin}")
                car.attempts = (car.attempts or 0) + 1
                car.has_correct_vin = False
                add_reason(car, "upstream parser error;")
                db.commit()
                return {"status": "exception", "vin": vin}

            # payload-level error
            if data.get("error"):
                # let outer except rollback — nothing persisted from this branch
                raise ValueError(f"Scraping error: {data['error']}")

            # --------------------------
            # 3) Field updates & flags
            # --------------------------
            car.owners = data.get("owners")
            car.has_correct_vin = True
            

            # mileage correctness: True лише якщо обидва наявні та рівні
            incoming_mileage = data.get("mileage")
            if incoming_mileage is not None and car.mileage is not None:
                try:
                    car.has_correct_mileage = int(car.mileage) == int(incoming_mileage)
                except Exception:
                    car.has_correct_mileage = False
            else:
                car.has_correct_mileage = False

            # accidents vs assessments
            car.accident_count = data.get("accident_count", 0)
            if car.condition_assessments and car.accident_count == 0:
                car.has_correct_accidents = False
                add_reason(car, "accident count mismatch with assessments;")
            elif car.accident_count > 0 and not car.condition_assessments:
                car.has_correct_accidents = False
                add_reason(car, "accidents present but no assessments;")
            else:
                car.has_correct_accidents = True

            # --------------------------
            # 4) Avg price / ROI / Fees
            # --------------------------
            prices = [int(data.get(k)) for k in ("jd", "d_max", "manheim") if data.get(k)]
            car.avg_market_price = int(sum(prices) / len(prices)) if prices else 0

            default_roi = _load_default_roi(db)
            if default_roi and car.avg_market_price:
                inv = car.avg_market_price / (1 + default_roi.roi / 100.0)
                car.predicted_total_investments = inv
                car.predicted_profit_margin_percent = default_roi.profit_margin
                car.predicted_profit_margin = car.avg_market_price * (
                    default_roi.profit_margin / 100.0
                )
            else:
                car.predicted_total_investments = 0.0
                car.predicted_profit_margin_percent = 0.0
                car.predicted_profit_margin = 0.0

            fees = _load_fees(
                db, car.auction, float(car.predicted_total_investments or 0.0)
            )
            car.auction_fee = _apply_fees(
                float(car.predicted_total_investments or 0.0), fees
            )

            car.suggested_bid = int(
                (car.predicted_total_investments or 0.0) - (car.sum_of_investments or 0.0)
            )
            car.predicted_roi = (
                default_roi.roi
                if (default_roi and (car.predicted_total_investments or 0.0) > 0)
                else 0.0
            )

            # --------------------------------
            # 5) Baseline recommendation logic
            # --------------------------------
            if (
                car.recommendation_status != RecommendationStatus.NOT_RECOMMENDED
                and (not car.recommendation_status_reasons or car.recommendation_status_reasons == "")
            ):
                car.recommendation_status = RecommendationStatus.RECOMMENDED

            # --------------------------
            # 6) HTML to S3 (optional)
            # --------------------------
            html_data = data.get("html_data")
            if html_data:
                s3_storage = S3StorageClient(
                    endpoint_url=settings.S3_STORAGE_ENDPOINT,
                    access_key=settings.S3_STORAGE_ACCESS_KEY,
                    secret_key=settings.S3_STORAGE_SECRET_KEY,
                    bucket_name=settings.S3_BUCKET_NAME,
                )
                file_key = f"auto_checks/{vin}/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_report.html"
                s3_storage.upload_fileobj_sync(file_key, BytesIO(html_data.encode("utf-8")))
                screenshot_url = f"{settings.S3_STORAGE_ENDPOINT}/{settings.S3_BUCKET_NAME}/{file_key}"
                db.add(AutoCheckModel(car_id=car.id, screenshot_url=screenshot_url))

            # success marker
            car.is_checked = True
            car.relevance = RelevanceStatus.ACTIVE
            car.attempts = (car.attempts or 0) + 1

            db.add(car)
            db.commit()
            logger.info(f"parse_and_update_car: updated VIN={vin}")
            return {"status": "success", "vin": vin}

        except Exception as e:
            db.rollback()
            logger.error(f"parse_and_update_car failed for VIN {vin}: {e}", exc_info=True)
            raise



def _norm_site(val) -> str:
    s = str(val or "").strip().lower()
    if s in {"1", "copart", "copart.com"}:
        return "copart"
    if s in {"2", "iaai", "iaa", "iaai.com"}:
        return "iaai"
    return s

@app.task(name="tasks.task.update_car_bids")
def update_car_bids() -> Dict[str, Any]:
    """
    Pull current bids from the parser and update active vehicles.
    Match by (lot + auction) case-insensitively; if not found — fallback by lot only.
    """
    logger.info("update_car_bids: start")
    updated = 0

    with SessionLocal() as db:
        try:
            # take active vehicles with valid lot/auction
            rows = db.execute(
                select(CarModel.id, CarModel.lot, CarModel.auction)
                .where(
                    and_(
                        CarModel.relevance == RelevanceStatus.ACTIVE,
                        CarModel.lot.isnot(None),
                        CarModel.auction.isnot(None),
                        or_(
                            CarModel.date.isnot(None),
                            func.lower(CarModel.auction_name) == "buynow",
                        )
                    )
                )
            ).all()
            cars = [{"id": r.id, "lot": r.lot, "auction": r.auction} for r in rows]
            if not cars:
                return {"status": "success", "updated_cars": 0}

            # call the parser
            resp = http_post_with_retries(
                url="http://parsers:8001/api/v1/parsers/scrape/current_bid",
                json={"items": [{"id": c["id"], "source": c["auction"], "lot": c["lot"]} for c in cars]},
                headers={"X-Auth-Token": settings.PARSERS_AUTH_TOKEN},
                timeout=900.0,
            )
            payload = resp.json()
            bids = payload.get("bids", []) if isinstance(payload, dict) else payload

            for item in bids:
                try:
                    # lot_id like "68271795-1" -> take the left part
                    lot_raw = str(item.get("lot_id") or "").split("-")[0].strip()
                    if not lot_raw.isdigit():
                        logger.debug("skip: bad lot_id=%r", item.get("lot_id"))
                        continue
                    lot = int(lot_raw)

                    site = _norm_site(item.get("site"))
                    pre_bid = item.get("pre_bid")
                    if pre_bid is None and pre_bid != 0:
                        logger.debug("skip: no pre_bid for lot=%s site=%s", lot, site)
                        continue

                    # 1) match by lot + auction (case-insensitive)
                    stmt = (
                        select(CarModel)
                        .where(
                            and_(
                                CarModel.lot == lot,
                                func.lower(func.coalesce(CarModel.auction, "")) == site,
                            )
                        )
                        .with_for_update(skip_locked=True)
                    )
                    car = db.execute(stmt).scalars().first()

                    # 2) fallback — by lot only (in case of auction name discrepancies in DB)
                    if not car:
                        # stmt2 = (
                        #     select(CarModel)
                        #     .where(CarModel.lot == lot)
                        #     .with_for_update(skip_locked=True)
                        # )
                        # car = db.execute(stmt2).scalars().first()
                        # if not car:
                        #     logger.debug("not found: lot=%s site=%s", lot, site)
                        continue

                    # update bids/status
                    try:
                        car.current_bid = int(float(pre_bid))
                    except (ValueError, TypeError):
                        logger.debug("skip: invalid pre_bid=%r lot=%s", pre_bid, lot)
                        continue

                    if car.suggested_bid is not None:
                        if car.current_bid > car.suggested_bid:
                            car.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                            reasons = (car.recommendation_status_reasons or "")
                            if "suggested bid < current bid;" not in reasons:
                                car.recommendation_status_reasons = (reasons + "suggested bid < current bid;").strip()
                        else:
                            # current bid does not exceed suggested
                            if car.recommendation_status_reasons:
                                car.recommendation_status_reasons = car.recommendation_status_reasons.replace(
                                    "suggested bid < current bid;", ""
                                )
                            if not car.recommendation_status_reasons:
                                car.recommendation_status = RecommendationStatus.RECOMMENDED

                    base = (car.sum_of_investments or 0) + (car.current_bid or 0)
                    if car.avg_market_price is not None and base > 0:
                        car.predicted_profit_margin = float(car.avg_market_price) - float(base)
                        car.predicted_roi = (car.predicted_profit_margin / base) * 100.0

                    updated += 1

                except Exception as e:
                    logger.info("update_car_bids: skipped lot=%r due to: %s", item.get("lot_id"), e)
                    continue

            db.commit()
            logger.info("update_car_bids: updated=%s", updated)
            return {"status": "success", "updated_cars": updated}

        except Exception:
            db.rollback()
            logger.exception("update_car_bids failed")
            raise

def _coerce_amount_and_percent(v) -> tuple[float, bool]:
    """Accept number or {'amount': num, 'percent': bool}. Return (amount, is_percent)."""
    if isinstance(v, dict):
        amt = v.get("amount", 0.0)
        pct = bool(v.get("percent", False))
        return float(amt), pct
    return float(v), False

def _inner_range_map(maybe_wrapped: dict) -> dict:
    """
    Unwrap one level if the dict is a container with a single section key
    like {'secured': {...}} or {'live_bid': {...}}. If already ranges -> return as is.
    """
    if not isinstance(maybe_wrapped, dict):
        return {}
    # If keys look like labels, not ranges, unwrap one layer
    label_like = {"secured", "unsecured", "live_bid", "online", "onsite", "credit", "cash"}
    keys = list(maybe_wrapped.keys())
    if len(keys) == 1 and keys[0] in label_like and isinstance(maybe_wrapped[keys[0]], dict):
        return maybe_wrapped[keys[0]]
    return maybe_wrapped

_MAX_PRICE = 1_000_000.0

def _parse_range_key(k: str) -> tuple[float, float]:
    """
    Parse 'min-max', 'min+', '+min', single value, or empty.
    - '8000.00+' -> (8000.0, _MAX_PRICE)
    - '+15000'   -> (15000.0, _MAX_PRICE)
    - '0.00-100.00' -> (0.0, 100.0)
    - '' or None -> (0.0, _MAX_PRICE)
    """
    if not k:
        return 0.0, _MAX_PRICE
    s = k.strip()
    # guard against accidental labels like 'secured'
    if not any(ch.isdigit() for ch in s):
        return 0.0, _MAX_PRICE
    if "-" in s:
        a, b = s.split("-", 1)
        a = a.replace("$", "").replace(",", "").replace("+", "").strip()
        b = b.replace("$", "").replace(",", "").replace("+", "").strip()
        p_from = float(a or 0.0)
        p_to = float(b) if b else _MAX_PRICE
        return p_from, p_to
    if s.endswith("+") or s.startswith("+"):
        base = s.replace("$", "").replace(",", "").replace("+", "").strip()
        p_from = float(base or 0.0)
        return p_from, _MAX_PRICE
    # single number
    single = float(s.replace("$", "").replace(",", ""))
    return single, single

@app.task(name="tasks.task.update_fees")
def update_car_fees() -> Dict[str, Any]:
    """
    Pull the fee table from the parser and update 'copart'.
    """
    logger.info("update_car_fees: start")
    with SessionLocal() as db:
        try:
            with httpx.Client(timeout=60) as client:
                r = client.get("http://parsers:8001/api/v1/parsers/scrape/fees")
                r.raise_for_status()
                payload = r.json()

            fees_data = payload["fees"]["copart"]["fees"]

            # Wipe old Copart fees
            db.execute(delete(FeeModel).where(FeeModel.auction == "copart"))

            # --- Bidding fees ---
            bidding_section = fees_data.get("bidding_fees", {})
            bidding_secured = bidding_section.get("secured", {})
            bidding_ranges = _inner_range_map(_inner_range_map(bidding_secured))
            for rng_key, raw in bidding_ranges.items():
                amount, is_percent = _coerce_amount_and_percent(raw)
                p_from, p_to = _parse_range_key(rng_key)
                db.add(FeeModel(
                    auction="copart",
                    fee_type="bidding_fees",
                    amount=amount,
                    percent=is_percent,
                    price_from=p_from,
                    price_to=p_to,
                ))

            # --- Virtual bid fee ---
            vbf_section = fees_data.get("virtual_bid_fee", {})
            vbf_live = vbf_section.get("live_bid", {})
            vbf_ranges = _inner_range_map(vbf_live)
            for rng_key, raw in vbf_ranges.items():
                amount, is_percent = _coerce_amount_and_percent(raw)
                p_from, p_to = _parse_range_key(rng_key)
                db.add(FeeModel(
                    auction="copart",
                    fee_type="virtual_bid_fee",
                    amount=amount,
                    percent=is_percent,
                    price_from=p_from,
                    price_to=p_to,
                ))

            # --- Gate fee ---
            gate = fees_data.get("gate_fee", {})
            if isinstance(gate, dict) and "amount" in gate:
                db.add(FeeModel(
                    auction="copart",
                    fee_type="gate_fee",
                    amount=float(gate["amount"]),
                    percent=False,
                    price_from=0.0,
                    price_to=_MAX_PRICE,
                ))

            # --- Environmental fee ---
            env = fees_data.get("environmental_fee", {})
            if isinstance(env, dict) and "amount" in env:
                db.add(FeeModel(
                    auction="copart",
                    fee_type="environmental_fee",
                    amount=float(env["amount"]),
                    percent=False,
                    price_from=0.0,
                    price_to=_MAX_PRICE,
                ))

            db.commit()
            logger.info("update_car_fees: OK")
            return {"status": "success"}

        except Exception:
            db.rollback()
            logger.exception("update_car_fees failed")
            raise





# --- kickoff for filter: sync + gevent ---
@app.task(name="tasks.task.kickoff_parse_for_filter")
def kickoff_parse_for_filter(filter_id: int, batch_size: int = 100, stream_chunk: int = 400) -> dict:
    """
    One lightweight task: reads filter conditions, streams all vehicles, and sends parse_and_update_car sub-tasks in batches.
    - batch_size: how many tasks to send to the broker at once
    - stream_chunk: a hint to the driver/ORM for streaming DB results
    """
    with SessionLocal() as session:
        filt = session.get(FilterModel, filter_id)
        if not filt:
            return {"status": "error", "reason": "filter_not_found", "filter_id": filter_id}

        conditions = [
            CarModel.make == filt.make,
            CarModel.year >= (filt.year_from or 0),
            CarModel.year <= (filt.year_to or 3000),
            CarModel.mileage >= (filt.odometer_min or 0),
            CarModel.mileage <= (filt.odometer_max or 10_000_000),
            CarModel.avg_market_price.is_(None),
            CarModel.is_checked.is_(False),
            CarModel.attempts < 3 
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

    batch: List[Dict[str, Any]] = []
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
    return {"status": "ok", "filter_id": filter_id, "enqueued": enqueued}


@app.task(name="tasks.task.parse_and_update_cars_with_expired_auction_date")
def parse_and_update_cars_with_expired_auction_date() -> Dict[str, Any]:
    """
    Synchronous task:
    - Selects ACTIVE vehicles with CarModel.date <= now()
    - Pulls fresh data from parser:
        - 404 -> relevance = ARCHIVAL
        - 200 -> updates the existing vehicle from payload (no creation)
    - Finally performs a transactional hard cleanup:
        - delete dependent rows (user_likes, auto_checks) for cars with relevance=IRRELEVANT and past date
        - delete the cars themselves
    """
    stats = {"checked": 0, "archived": 0, "updated": 0, "errors": 0, "cleanup": {"likes": 0, "auto_checks": 0, "cars": 0}}

    def _apply_update_from_schema(db: Session, existing_vehicle: CarModel, vehicle_info: CarCreateSchema) -> None:
        """
        Update an existing CarModel from CarCreateSchema.
        Mirrors business rules: fuel_type / transmision / risk assessments / bids / history / photos.
        """
        # 1) Simple scalar fields
        for field, value in vehicle_info.dict(
            exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}
        ).items():
            if (value is not None) or (field == "date"):
                setattr(existing_vehicle, field, value)

                # business rules
                if field == "fuel_type" and value not in ["Gasoline", "Flexible Fuel", "Unknown"]:
                    existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                    if not existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons = f"{value};"
                    elif f"{value};" not in existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons += f"{value};"

                if field == "transmision" and value != "Automatic":
                    existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                    if not existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons = f"{value};"
                    elif f"{value};" not in existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons += f"{value};"

        # 2) Photos — only add missing URLs
        existing_photo_urls = {p.url for p in existing_vehicle.photos}
        new_photos = []
        if vehicle_info.photos:
            for p in vehicle_info.photos:
                if p.url not in existing_photo_urls:
                    new_photos.append(PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=False))
        if vehicle_info.photos_hd:
            for p in vehicle_info.photos_hd:
                if p.url not in existing_photo_urls:
                    new_photos.append(PhotoModel(url=p.url, car_id=existing_vehicle.id, is_hd=True))
        if new_photos:
            db.add_all(new_photos)

        # 3) Condition assessments — full replacement
        db.execute(delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == existing_vehicle.id))
        if vehicle_info.condition_assessments:
            for a in vehicle_info.condition_assessments:
                db.add(ConditionAssessmentModel(
                    type_of_damage=a.type_of_damage,
                    issue_description=a.issue_description,
                    car_id=existing_vehicle.id,
                ))
                if a.issue_description in [
                    "Rejected Repair", "Burn Engine", "Mechanical", "Replaced Vin", "Burn",
                    "Undercarriage", "Water/Flood", "Burn Interior", "Rollover",
                ]:
                    existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                    reason = f"{a.issue_description};"
                    if not existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons = reason
                    elif reason not in existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons += reason

        # 4) Bid higher than suggested
        if (
            vehicle_info.current_bid is not None
            and existing_vehicle.suggested_bid is not None
            and vehicle_info.current_bid > existing_vehicle.suggested_bid
        ):
            existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED

        # 5) Sales history — add only if DB still doesn't have it
        if not existing_vehicle.sales_history and vehicle_info.sales_history:
            if len(vehicle_info.sales_history) >= 4:
                existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                reason = f"sales at auction in the last 3 years: {len(vehicle_info.sales_history)};"
                if not existing_vehicle.recommendation_status_reasons:
                    existing_vehicle.recommendation_status_reasons = reason
                elif reason not in existing_vehicle.recommendation_status_reasons:
                    existing_vehicle.recommendation_status_reasons += reason

            buf = []
            for h in vehicle_info.sales_history:
                m = CarSaleHistoryModel(**h.dict(), car_id=existing_vehicle.id)
                if not m.source:
                    m.source = "Unknown"
                buf.append(m)
            if buf:
                db.add_all(buf)

        db.add(existing_vehicle)

    with SessionLocal() as db:
        # Select candidate VINs (ACTIVE with past date)
        vin_rows = db.execute(
            select(CarModel.vin).where(
                and_(
                    CarModel.relevance == RelevanceStatus.ACTIVE,
                    CarModel.date <= func.now(),
                )
            )
        ).scalars().all()
        logger.info("Expired-auction: selected %d VINs to process", len(vin_rows))

        for vin in vin_rows:
            stats["checked"] += 1

            # 1) Query parser
            try:
                resp = httpx.get(f"http://parsers:8001/api/v1/apicar/{vin}", timeout=30.0)
            except Exception as e:
                logger.exception("VIN %s: HTTP request failed: %s", vin, e)
                stats["errors"] += 1
                continue

            # 2) 404 -> ARCHIVAL
            if resp.status_code == 404:
                try:
                    existing = db.execute(
                        select(CarModel).where(CarModel.vin == vin)
                    ).scalars().first()
                    if existing:
                        existing.relevance = RelevanceStatus.ARCHIVAL
                        existing.is_manually_upserted = False
                        db.add(existing)
                        db.commit()
                        logger.info("VIN %s marked as ARCHIVAL after 404 from parser", vin)
                        stats["archived"] += 1
                    else:
                        logger.info("VIN %s not found when marking as ARCHIVAL", vin)
                except Exception as e:
                    db.rollback()
                    logger.exception("VIN %s: failed to mark ARCHIVAL: %s", vin, e)
                    stats["errors"] += 1
                continue

            # 3) Other HTTP errors
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.exception("VIN %s: parser returned %s", vin, e)
                stats["errors"] += 1
                continue

            # 4) Payload validation
            try:
                payload = resp.json()
                vehicle_info = CarCreateSchema.model_validate(payload)
            except Exception as e:
                logger.exception("VIN %s: payload validation failed: %s", vin, e)
                stats["errors"] += 1
                continue

            # 5) Update existing vehicle (no creation)
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

                # If still past date and not BuyNow -> archive
                if (
                    existing_vehicle.auction_name
                    and existing_vehicle.auction_name.lower() != "buynow"
                    and (
                        (existing_vehicle.date is not None and existing_vehicle.date <= func.now())
                        or existing_vehicle.date is None
                    )
                ):
                    existing_vehicle.relevance = RelevanceStatus.ARCHIVAL
                    existing_vehicle.is_manually_upserted = False
                    logger.info("VIN %s archived after update (date still in the past)", vin)

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

        # === Transactional hard cleanup (dependents -> cars) ===
        try:
            # Use one atomic transaction to avoid FK errors and partial states
            with db.begin():
                # 1) Delete user_likes referencing IRRELEVANT + past-date cars
                del_likes = db.execute(text("""
                    DELETE FROM user_likes ul
                    USING cars c
                    WHERE ul.car_id = c.id
                      AND c.date < NOW()
                      AND c.relevance = 'IRRELEVANT'
                """))

                # 2) Delete auto_checks referencing those cars (if you store screenshots/logs)
                del_checks = db.execute(text("""
                    DELETE FROM auto_checks ac
                    USING cars c
                    WHERE ac.car_id = c.id
                      AND c.date < NOW()
                      AND c.relevance = 'IRRELEVANT'
                """))

                # 3) Delete cars themselves
                del_cars = db.execute(text("""
                    DELETE FROM cars
                    WHERE date < NOW()
                      AND relevance = 'IRRELEVANT'
                """))

            stats["cleanup"]["likes"] = del_likes.rowcount or 0
            stats["cleanup"]["auto_checks"] = del_checks.rowcount or 0
            stats["cleanup"]["cars"] = del_cars.rowcount or 0

            logger.info(
                "Cleanup done: likes=%d, auto_checks=%d, cars=%d",
                stats["cleanup"]["likes"],
                stats["cleanup"]["auto_checks"],
                stats["cleanup"]["cars"],
            )

        except Exception as e:
            db.rollback()
            logger.exception("Cleanup delete failed: %s", e)

    logger.info("Expired auction update finished: %s", stats)
    return stats

