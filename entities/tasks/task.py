# tasks.py — синхронні Celery-таски для gevent-пулу
# --------------------------------------------------
import os
# if os.environ.get("CELERY_GEVENT", "0") == "1":
#     from gevent import monkey
#     monkey.patch_all()

import logging
import os
import time
import asyncio
import anyio
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Optional, List, Callable, Union

import httpx
from sqlalchemy import and_, delete, func, select, create_engine, or_
from sqlalchemy.orm import sessionmaker, Session, selectinload

from core.celery_config import app
from core.config import settings
from db.session import POSTGRESQL_DATABASE_URL
from models.admin import ROIModel, FilterModel
from models.vehicle import (
    AutoCheckModel,
    CarModel,
    CarSaleHistoryModel,
    FeeModel,
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

# Глушимо надмірні логи SQLAlchemy (INSERT ... VALUES ..., pool і т.д.)
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
# Small util: виконає async-функцію через asyncio.run, якщо вона coroutine; інакше — як є
# =========================
def run_async(func: Callable[..., Any], *args, **kwargs) -> Union[Any, asyncio.Task]:
    try:
        loop = asyncio.get_running_loop()   # є активний loop у цьому потоці
    except RuntimeError:
        # loop не запущено — можна стартувати власний
        return anyio.run(func, *args, **kwargs)
    else:
        # loop вже працює — плануємо завдання і ПОВЕРТАЄМО Task
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


# =========================
# Celery Tasks (sync)
# =========================
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
    Повністю синхронна таска:
    - тягне sales history напряму
    - дергає парсер
    - оновлює машину
    - зберігає html у S3 (якщо є)
    """
    logger.info(f"parse_and_update_car: VIN={vin}")

    with SessionLocal() as db:
        # 1) Sales history inline
        try:
            url = f"http://parsers:8001/api/v1/apicar/get/{vin}"
            headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
            resp = http_get_with_retries(url, headers=headers, timeout=30.0)
            resp.raise_for_status()
            result = CarCreateSchema.model_validate(resp.json())

            logger.info(f"Successfully scraped sales history data {result.sales_history}")

            car = db.execute(
                select(CarModel).where(CarModel.vin == vin).limit(1)
            ).scalars().first()
            if car:
                sale_history_data = result.sales_history
                if len(sale_history_data) >= 4:
                    logger.debug(
                        f"More than 3 sales history records for VIN={vin}. Not recommended."
                    )
                    car.recomendation_status = RecommendationStatus.NOT_RECOMMENDED
                    if not car.recommendation_status_reasons:
                        car.recommendation_status_reasons = (
                            f"sales at auction in the last 3 years: {len(sale_history_data)};"
                        )
                    else:
                        car.recommendation_status_reasons += (
                            f"sales at auction in the last 3 years: {len(sale_history_data)};"
                        )
                    db.add(car)
                    db.flush()

                for history_data in sale_history_data:
                    sales_history = CarSaleHistoryModel(
                        **history_data.dict(), car_id=car.id
                    )
                    if not sales_history.source:
                        sales_history.source = "Unknown"
                    db.add(sales_history)

                db.commit()
        except Exception as e:
            logger.warning(f"Sales history fetch failed for {vin}: {e}")

        try:
            # 2) шукаємо «поточну» машину
            current_date = datetime.utcnow().date()
            q_current = (
                select(CarModel)
                .where(
                    and_(
                        func.lower(CarModel.vehicle) == func.lower(car_name)
                        if car_name
                        else True,
                        CarModel.mileage.between(mileage - 1500, mileage + 1500)
                        if mileage
                        else True,
                        func.date(CarModel.created_at) == current_date,
                        CarModel.predicted_total_investments.isnot(None),
                    )
                )
                .limit(1)
            )
            existing_car = db.execute(q_current).scalars().first()

            # 3) виклик парсера
            only_history = "true" if existing_car else "false"
            url = (
                "http://parsers:8001/api/v1/parsers/scrape/dc"
                f"?car_vin={vin}"
                f"&car_mileage={mileage}"
                f"&car_name={car_name}"
                f"&car_engine={car_engine}"
                f"&car_make={car_make}"
                f"&car_model={car_model}"
                f"&car_year={car_year}"
                f"&car_transmison={car_transmison}"
                f"&only_history={only_history}"
            )
            headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
            resp = http_get_with_retries(url, headers=headers, timeout=300.0)
            data = resp.json()

            # 4) оновлення машини під lock
            car_q = (
                select(CarModel)
                .where(CarModel.vin == vin)
                .options(selectinload(CarModel.condition_assessments))
                .with_for_update()
            )
            car = db.execute(car_q).scalars().first()
            if not car:
                raise ValueError(f"Car with VIN {vin} not found")

            if data.get("error"):
                car.has_correct_vin = False
                raise ValueError(f"Scraping error: {data['error']}")

            car.owners = data.get("owners")
            car.has_correct_vin = True

            if data.get("mileage") is not None and car.mileage is None:
                try:
                    car.has_correct_mileage = int(car.mileage) == int(
                        data.get("mileage", 0)
                    )
                except Exception:
                    car.has_correct_mileage = False
            else:
                car.has_correct_mileage = False

            car.accident_count = data.get("accident_count", 0)
            if car.condition_assessments and car.accident_count == 0:
                car.has_correct_accidents = False
            elif car.accident_count > 0 and not car.condition_assessments:
                car.has_correct_accidents = False
            else:
                car.has_correct_accidents = True

            # середні ціни
            prices = [
                int(data.get(k)) for k in ("jd", "d_max", "manheim") if data.get(k)
            ]
            if existing_car and existing_car.avg_market_price:
                car.avg_market_price = existing_car.avg_market_price
            else:
                car.avg_market_price = (
                    int(sum(prices) / len(prices)) if prices else 0
                )

            # ROI / інвестиції / маржа
            default_roi = _load_default_roi(db)
            if existing_car:
                car.predicted_total_investments = (
                    existing_car.predicted_total_investments
                )
                car.predicted_profit_margin = existing_car.predicted_profit_margin
                car.predicted_profit_margin_percent = (
                    existing_car.predicted_profit_margin_percent
                )
            elif default_roi:
                car.predicted_total_investments = (
                    car.avg_market_price / (1 + default_roi.roi / 100.0)
                    if car.avg_market_price
                    else 0.0
                )
                car.predicted_profit_margin_percent = default_roi.profit_margin
                car.predicted_profit_margin = car.avg_market_price * (
                    default_roi.profit_margin / 100.0
                )
            else:
                car.predicted_total_investments = 0.0
                car.predicted_profit_margin_percent = 0.0
                car.predicted_profit_margin = 0.0

            # комісії аукціону
            fees = _load_fees(
                db, car.auction, float(car.predicted_total_investments or 0.0)
            )
            car.auction_fee = _apply_fees(
                float(car.predicted_total_investments or 0.0), fees
            )

            # suggested bid / ROI
            car.suggested_bid = int(
                (car.predicted_total_investments or 0.0)
                - (car.sum_of_investments or 0.0)
            )
            car.predicted_roi = (
                default_roi.roi
                if (default_roi and (car.predicted_total_investments or 0.0) > 0)
                else 0.0
            )

            if not car.recommendation_status_reasons or car.recommendation_status_reasons == "":
                car.recommendation_status = RecommendationStatus.RECOMMENDED

            # 5) HTML в S3
            html_data = data.get("html_data")
            if html_data:
                s3_storage = S3StorageClient(
                    endpoint_url=settings.S3_STORAGE_ENDPOINT,
                    access_key=settings.S3_STORAGE_ACCESS_KEY,
                    secret_key=settings.S3_STORAGE_SECRET_KEY,
                    bucket_name=settings.S3_BUCKET_NAME,
                )
                file_key = f"auto_checks/{vin}/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_report.html"
                s3_storage.upload_fileobj_sync(
                    file_key, BytesIO(html_data.encode("utf-8"))
                )
                screenshot_url = f"{settings.S3_STORAGE_ENDPOINT}/{settings.S3_BUCKET_NAME}/{file_key}"
                db.add(AutoCheckModel(car_id=car.id, screenshot_url=screenshot_url))

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
    Тягнемо поточні біди з парсера та оновлюємо активні авто.
    Матчимо по (lot + auction) без урахування регістру; якщо не знайшли — фолбек по одному lot.
    """
    logger.info("update_car_bids: start")
    updated = 0

    with SessionLocal() as db:
        try:
            # беремо активні з валідними lot/auction
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

            # дернемо парсер
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
                    # lot_id типу "68271795-1" -> беремо ліву частину
                    lot_raw = str(item.get("lot_id") or "").split("-")[0].strip()
                    if not lot_raw.isdigit():
                        logger.debug("skip: bad lot_id=%r", item.get("lot_id"))
                        continue
                    lot = int(lot_raw)

                    site = _norm_site(item.get("site"))
                    pre_bid = item.get("pre_bid")
                    if pre_bid is None:
                        logger.debug("skip: no pre_bid for lot=%s site=%s", lot, site)
                        continue

                    # 1) match по lot + auction (без регістру)
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

                    # 2) fallback — лише lot (на випадок розбіжностей у назві аукціону в БД)
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

                    # апдейт ставок/статусу
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
                            # поточна ставка не перевищує рекомендовану
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

@app.task(name="tasks.task.update_fees")
def update_car_fees() -> Dict[str, Any]:
    """
    Перетягуємо fee-таблицю з парсера та оновлюємо 'copart'.
    """
    logger.info("update_car_fees: start")

    with SessionLocal() as db:
        try:
            with httpx.Client(timeout=60) as client:
                response = client.get("http://parsers:8001/api/v1/parsers/scrape/fees")
                response.raise_for_status()
                fees_data = response.json()["fees"]["copart"]["fees"]

            # wipe old
            db.execute(delete(FeeModel).where(FeeModel.auction == "copart"))

            # map
            fee_mappings = {
                "bidding_fees": fees_data["bidding_fees"]["secured"]["secured"],
                "gate_fee": {"amount": fees_data["gate_fee"]["amount"]},
                "virtual_bid_fee": {k: v for k, v in fees_data["virtual_bid_fee"]["live_bid"].items()},
                "environmental_fee": {"amount": fees_data["environmental_fee"]["amount"]},
            }

            for fee_type, fee_values in fee_mappings.items():
                if not isinstance(fee_values, dict):
                    continue
                for price_range, amount_str in fee_values.items():
                    amount = float(amount_str)
                    is_percent = False

                    # евристика: відсоткова ставка у secured bidding_fees
                    if fee_type == "bidding_fees" and price_range == "0.00+" and 0 < amount < 10:
                        is_percent = True

                    if "-" in price_range:
                        price_from, price_to = map(float, price_range.replace("+", "").split("-"))
                    else:
                        price_from = 0.0 if price_range == "0.00+" else 15000.0
                        price_to = 10_000_000.0

                    fee = FeeModel(
                        auction="copart",
                        fee_type=fee_type,
                        amount=amount,
                        percent=is_percent,
                        price_from=price_from,
                        price_to=price_to,
                    )
                    db.add(fee)

            db.commit()
            logger.info("update_car_fees: OK")
            return {"status": "success", "count": 4}

        except httpx.HTTPStatusError as e:
            db.rollback()
            logger.error(f"update_car_fees HTTP error: {e}", exc_info=True)
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"update_car_fees failed: {e}", exc_info=True)
            raise


# --- kickoff для фільтра: sync + gevent ---
@app.task(name="tasks.task.kickoff_parse_for_filter")
def kickoff_parse_for_filter(filter_id: int, batch_size: int = 100, stream_chunk: int = 400) -> dict:
    """
    Одна легка задачка: читає умови фільтра, стрімить усі авто, і шле підзадачі parse_and_update_car пачками.
    - batch_size: скільки задач відправляти за раз у брокер
    - stream_chunk: підказка драйверу/ORM для стрімінгу результатів з БД
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
            .execution_options(stream_results=True, yield_per=stream_chunk)  # RAM-friendly
        )

        result = session.execute(stmt)

        batch: List[Dict[str, Any]] = []
        enqueued = 0

        for row in result.mappings():
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
