import os
import logging
import httpx
import asyncio
import anyio
from datetime import datetime
from io import BytesIO
import base64

from core.celery_config import app
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from models.vehicle import CarModel, AutoCheckModel, FeeModel
from models.admin import ROIModel
from db.session import POSTGRESQL_DATABASE_URL
from core.config import settings
from storages import S3StorageClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
# AsyncSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)

# def get_db():
#     return AsyncSessionFactory()


logger.info("S3 storage client initialized successfully")


async def http_get_with_retries(url: str, headers: dict = None, timeout: float = 30.0, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(f"HTTP GET attempt {attempt} failed: {e}")
            if attempt == max_retries:
                raise
            await asyncio.sleep(2**attempt)


async def http_post_with_retries(
    url: str, json: dict, headers: dict = None, timeout: float = 30.0, max_retries: int = 3
):
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=json, headers=headers)
                response.raise_for_status()
                return response
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(f"HTTP POST attempt {attempt} failed: {e}")
            if attempt == max_retries:
                raise
            await asyncio.sleep(2**attempt)


async def _parse_and_update_car_async(vin: str, car_name: str = None, car_engine: str = None):
    logger.info(f"Starting _parse_and_update_car_async for VIN: {vin}, car_name: {car_name}, car_engine: {car_engine}")
    engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
    AsyncSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with AsyncSessionFactory() as db:
        try:
            url = f"http://parsers:8001/api/v1/parsers/scrape/dc?car_vin={vin}&car_name={car_name}&car_engine={car_engine}"
            headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}

            response = await http_get_with_retries(url, headers=headers, timeout=300.0)
            data = response.json()

            if data.get("error"):
                raise ValueError(f"Scraping error: {data['error']}")

            screenshot_data = base64.b64decode(data["screenshot"]) if data.get("screenshot") else None

            query = select(CarModel).where(CarModel.vin == vin).with_for_update()
            result = await db.execute(query)
            car = result.scalars().first()

            if not car:
                raise ValueError(f"Car with VIN {vin} not found")

            car.owners = data.get("owners")
            if data.get("mileage") is not None:
                car.has_correct_mileage = car.mileage == data.get("mileage")
            car.accident_count = data.get("accident_count")

            prices = [data.get(k) for k in ["price", "retail", "manheim"] if data.get(k)]
            valid_prices = [int(float(p)) for p in prices if p]
            car.avg_market_price = int(sum(valid_prices) / len(valid_prices)) if valid_prices else 0

            roi_result = await db.execute(select(ROIModel).order_by(ROIModel.created_at.desc()))
            default_roi = roi_result.scalars().first()

            if default_roi:
                car.predicted_total_investment = (
                    (car.avg_market_price * 100) / (100 - default_roi.roi) if car.avg_market_price else 0
                )
                car.predicted_profit_margin_percent = default_roi.profit_margin
            else:
                car.predicted_total_investment = 0
                car.predicted_profit_margin_percent = 0

            fees_result = await db.execute(
                select(FeeModel).where(
                    FeeModel.auction == car.auction,
                    FeeModel.price_from <= car.avg_market_price,
                    FeeModel.price_to >= car.avg_market_price,
                )
            )
            fees = fees_result.scalars().all()
            car.auction_fee = sum(fee.amount for fee in fees)
            car.suggested_bid = int(car.predicted_total_investment - car.auction_fee)
            car.predicted_roi = default_roi.roi if car.total_investment > 0 else 0

            if screenshot_data:
                s3_storage = S3StorageClient(
                    endpoint_url=settings.S3_STORAGE_ENDPOINT,
                    access_key=settings.S3_STORAGE_ACCESS_KEY,
                    secret_key=settings.S3_STORAGE_SECRET_KEY,
                    bucket_name=settings.S3_BUCKET_NAME,
                )
                file_key = f"auto_checks/{vin}/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_screenshot.png"
                await s3_storage.upload_fileobj(file_key, BytesIO(screenshot_data))
                screenshot_url = f"{settings.S3_STORAGE_ENDPOINT}/{settings.S3_BUCKET_NAME}/{file_key}"
                db.add(AutoCheckModel(car_id=car.id, screenshot_url=screenshot_url))

            await db.commit()
            return {"status": "success", "vin": vin}

        except Exception as e:
            logger.error(f"Error updating car {vin}: {e}", exc_info=True)
            raise


@app.task(name="tasks.task.parse_and_update_car")
def parse_and_update_car(vin: str, car_name: str = None, car_engine: str = None):
    return anyio.run(_parse_and_update_car_async, vin, car_name, car_engine)


async def _update_car_bids_async():
    logger.info("Starting _update_car_bids_async")
    engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
    AsyncSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with AsyncSessionFactory() as db:
        try:
            current_time = datetime.utcnow()
            query = select(CarModel.id, CarModel.link, CarModel.lot).where(CarModel.date < current_time)
            result = await db.execute(query)
            cars = [{"id": r.id, "url": r.link, "lot": r.lot} for r in result.all()]

            if not cars:
                return {"status": "success", "count": 0}

            response = await http_post_with_retries(
                url="http://parsers:8001/api/v1/parsers/scrape/current_bid",
                json={"items": cars},
                headers={"X-Auth-Token": settings.PARSERS_AUTH_TOKEN},
                timeout=300.0,
            )
            data = response.json()

            for item in data:
                car_id, current_bid = item.get("id"), item.get("current_bid")
                if car_id and current_bid is not None:
                    result = await db.execute(select(CarModel).where(CarModel.id == car_id).with_for_update())
                    car = result.scalars().first()
                    if car:
                        car.current_bid = int(float(current_bid))

            await db.commit()
            return {"status": "success", "updated_cars": len(data)}

        except Exception as e:
            logger.error(f"Error in _update_car_bids_async: {e}", exc_info=True)
            raise


@app.task(name="tasks.task.update_car_bids")
def update_car_bids():
    return anyio.run(_update_car_bids_async)


async def _update_car_fees_async():
    logger.info("Starting _update_car_fees_async")
    engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
    AsyncSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with AsyncSessionFactory() as db:
        try:
            result = await db.execute(select(CarModel).options(selectinload(CarModel.fees)))
            cars = result.scalars().all()

            for car in cars:
                await db.execute(delete(FeeModel).where(FeeModel.car_id == car.id))
                for fee in getattr(car, "fees", []):
                    fee.car_id = car.id
                    db.add(fee)

            await db.commit()
            return {"status": "success", "count": len(cars)}

        except Exception as e:
            logger.error(f"Error in _update_car_fees_async: {e}", exc_info=True)
            raise


@app.task(name="tasks.task.update_car_fees")
def update_car_fees():
    return anyio.run(_update_car_fees_async)
