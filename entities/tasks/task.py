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
from sqlalchemy.orm import selectinload, sessionmaker
from sqlalchemy import delete, func, and_
from sqlalchemy.exc import SQLAlchemyError

from models.vehicle import CarModel, AutoCheckModel, FeeModel, RecommendationStatus
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


async def _parse_and_update_car_async(
    vin: str,
    car_name: str = None,
    car_engine: str = None,
    mileage: int = None,
    car_make: str = None,
    car_model: str = None,
    car_year: int = None,
    car_transmison: str = None,
):
    logger.info(
        f"Starting _parse_and_update_car_async for VIN: {vin}, car_name: {car_name}, car_engine: {car_engine}, mileage: {mileage}"
    )
    engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
    AsyncSessionFactory = sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with AsyncSessionFactory() as db:
        try:
            # Перевірка наявності автомобіля в базі
            current_date = datetime.utcnow().date()
            query = select(CarModel).where(
                and_(
                    func.lower(CarModel.vehicle) == func.lower(car_name) if car_name else True,
                    CarModel.mileage.between(mileage - 1500, mileage + 1500) if mileage else True,
                    func.date(CarModel.created_at) == current_date,
                    CarModel.predicted_total_investments.isnot(None),
                )
            )
            result = await db.execute(query)
            existing_car = result.scalars().first()

            # Формування URL для запиту до API
            only_history = "true" if existing_car else "false"
            url = f"http://parsers:8001/api/v1/parsers/scrape/dc?car_vin={vin}&car_mileage={mileage}&car_name={car_name}&car_engine={car_engine}&car_make={car_make}&car_model={car_model}&car_year={car_year}&car_transmison={car_transmison}&only_history={only_history}"
            headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}

            response = await http_get_with_retries(url, headers=headers, timeout=300.0)
            data = response.json()
            logger.info(
                f"Received data for VIN {vin}: {data.get('vehicle', 'No vehicle data')} - {data.get('vin', 'No VIN')} - {data.get('mileage', 'No mileage')} - {data.get('accident_count', 'No accident count')} - {data.get('owners', 'No owners')}"
            )

            html_data = data.get("html_data", None)

            query = select(CarModel).where(CarModel.vin == vin).options(selectinload(CarModel.condition_assessments)).with_for_update()
            result = await db.execute(query)
            car = result.scalars().first()
            if not car:
                raise ValueError(f"Car with VIN {vin} not found")
            if data.get("error"):
                car.has_correct_vin = False
                raise ValueError(f"Scraping error: {data['error']}")
            car.owners = data.get("owners")
            car.has_correct_vin = True
            if data.get("mileage") is not None:
                car.has_correct_mileage = int(car.mileage) == int(data.get("mileage", 0))
            if car.mileage is None or not car.has_correct_mileage:
                car.mileage = int(data.get("mileage", 0)) if data.get("mileage") else 0
            car.accident_count = data.get("accident_count", 0)
            if car.condition_assessments and car.accident_count == 0:
                car.has_correct_accidents = False
            elif car.accident_count > 0 and not car.condition_assessments:
                car.has_correct_accidents = False
            else:
                car.has_correct_accidents = True
                
            # car.recommendation_status = (
            #     RecommendationStatus.RECOMMENDED
            #     if car.accident_count <= 2 and car.has_correct_mileage and car.has_correct_accidents
            #     else RecommendationStatus.NOT_RECOMMENDED
            # )

            car.avg_market_price = (
                existing_car.avg_market_price
                if existing_car
                else (
                    int(
                        sum(
                            [
                                int(p)
                                for p in [data.get(k) for k in ["jd", "d_max", "manheim"] if data.get(k)]
                                if p
                            ]
                        )
                        / len([p for p in [data.get(k) for k in ["jd", "d_max", "manheim"] if data.get(k)] if p])
                        if [p for p in [data.get(k) for k in ["jd", "d_max", "manheim"] if data.get(k)] if p]
                        else [0]
                    )
                    if not existing_car
                    else existing_car.avg_market_price
                )
            )

            roi_result = await db.execute(select(ROIModel).order_by(ROIModel.created_at.desc()))
            default_roi = roi_result.scalars().first()

            if existing_car:
                car.predicted_total_investments = existing_car.predicted_total_investments
                car.predicted_profit_margin = existing_car.predicted_profit_margin
                car.predicted_profit_margin_percent = existing_car.predicted_profit_margin_percent
            elif default_roi:
                car.predicted_total_investments = (
                    car.avg_market_price / (1 + default_roi.roi / 100) if car.avg_market_price else 0
                )
                car.predicted_profit_margin_percent = default_roi.profit_margin
                car.predicted_profit_margin = car.avg_market_price * (default_roi.profit_margin / 100)
            else:
                car.predicted_total_investments = 0
                car.predicted_profit_margin_percent = 0

            fees_result = await db.execute(
                select(FeeModel).where(
                    FeeModel.auction == car.auction,
                    FeeModel.price_from <= car.avg_market_price,
                    FeeModel.price_to >= car.avg_market_price,
                )
            )
            fees = fees_result.scalars().all()

            # Calculate auction_fee considering percentage-based fees
            car.auction_fee = 0
            for fee in fees:
                if fee.percent:
                    # Calculate percentage-based fee
                    car.auction_fee += (fee.amount / 100) * car.predicted_total_investments
                else:
                    # Add fixed fee
                    car.auction_fee += fee.amount

            car.suggested_bid = int(car.predicted_total_investments - car.auction_fee)
            car.predicted_roi = default_roi.roi if car.predicted_total_investments > 0 else 0
            if not car.recommendation_status_reasons or car.recommendation_status_reasons == "":
                car.recommendation_status = RecommendationStatus.RECOMMENDED


            if html_data:
                s3_storage = S3StorageClient(
                    endpoint_url=settings.S3_STORAGE_ENDPOINT,
                    access_key=settings.S3_STORAGE_ACCESS_KEY,
                    secret_key=settings.S3_STORAGE_SECRET_KEY,
                    bucket_name=settings.S3_BUCKET_NAME,
                )
                file_key = f"auto_checks/{vin}/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_report.html"
                await s3_storage.upload_fileobj(file_key, BytesIO(html_data.encode("utf-8")))
                screenshot_url = f"{settings.S3_STORAGE_ENDPOINT}/{settings.S3_BUCKET_NAME}/{file_key}"
                db.add(AutoCheckModel(car_id=car.id, screenshot_url=screenshot_url))
                await db.flush()

            db.add(car)
            await db.commit()
            return {"status": "success", "vin": vin}

        except Exception as e:
            logger.error(f"Error updating car {vin}: {e}", exc_info=True)
            raise


@app.task(name="tasks.task.parse_and_update_car")
def parse_and_update_car(
    vin: str,
    car_name: str = None,
    car_engine: str = None,
    mileage: int = None,
    car_make: str = None,
    car_model: str = None,
    car_year: int = None,
    car_transmison: str = None,
):
    logger.info(f"Scheduling parse_and_update_car for VIN: {vin}, car_name: {car_name}, car_engine: {car_engine}")
    return anyio.run(
        _parse_and_update_car_async, vin, car_name, car_engine, mileage, car_make, car_model, car_year, car_transmison
    )


async def _update_car_bids_async():
    logger.info("Starting _update_car_bids_async")
    engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
    AsyncSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with AsyncSessionFactory() as db:
        try:
            current_time = datetime.utcnow()
            query = select(CarModel.id, CarModel.link, CarModel.lot).where(CarModel.date > current_time)
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
            logger.info(f"Received {data} items to update bids")

            for item in data.get("bids"):
                car_id, current_bid = item.get("id"), item.get("value")
                if car_id and current_bid is not None:
                    result = await db.execute(select(CarModel).where(CarModel.id == car_id).with_for_update())
                    car = result.scalars().first()
                    if car:
                        car.current_bid = int(float(current_bid))
                        if car.suggested_bid and car.current_bid > car.suggested_bid:
                            car.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                            car.predicted_total_investments = car.sum_of_investments + car.current_bid
                            car.predicted_roi = (car.avg_market_price - car.predicted_total_investments) / car.predicted_total_investments * 100
                            car.predicted_profit_margin = car.avg_market_price - car.predicted_total_investments
                            if not car.recommendation_status_reasons:
                                car.recommendation_status_reasons = "suggested bid < current bid;"
                            elif "suggested bid < current bid" in car.recommendation_status_reasons:
                                pass
                            else:
                                car.recommendation_status_reasons += "suggested bid < current bid;"

            await db.commit()
            return {"status": "success", "updated_cars": len(data)}

        except Exception as e:
            logger.error(f"Error in _update_car_bids_async: {e}", exc_info=True)
            raise


@app.task(name="tasks.task.update_car_bids")
def update_car_bids():
    return anyio.run(_update_car_bids_async)


# Asynchronous function to update fees
async def _update_car_fees_async():
    logger.info("Starting _update_car_fees_async")
    engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
    AsyncSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with AsyncSessionFactory() as db:
        try:
            # Perform HTTP request to the endpoint
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get("http://parsers:8001/api/v1/parsers/scrape/fees")
                response.raise_for_status()  # Raise an exception for bad status codes
                fees_data = response.json()["fees"]["copart"]["fees"]

            # Delete all existing fees for auction 'copart'
            await db.execute(delete(FeeModel).where(FeeModel.auction == "copart"))
            logger.info("Deleted all existing fees for auction 'copart'")

            # Process different types of fees from the response
            fee_mappings = {
                "bidding_fees": fees_data["bidding_fees"]["secured"]["secured"],
                "gate_fee": {"amount": fees_data["gate_fee"]["amount"]},
                "virtual_bid_fee": {k: v for k, v in fees_data["virtual_bid_fee"]["live_bid"].items()},
                "environmental_fee": {"amount": fees_data["environmental_fee"]["amount"]},
            }

            for fee_type, fee_values in fee_mappings.items():
                if isinstance(fee_values, dict):  # Check if fee_values is a dictionary
                    for price_range, amount_str in fee_values.items():
                        amount = float(amount_str)
                        is_percent = False

                        # Logic to determine if the amount is a percentage
                        if fee_type == "bidding_fees" and price_range == "0.00+" and 0 < amount < 10:
                            is_percent = True  # Assume 5.75 is a percentage

                        if "-" in price_range:  # Price range (e.g., "0.00-49.99")
                            price_from, price_to = map(float, price_range.replace("+", "").split("-"))
                        else:  # Single value or "0.00+"
                            price_from = float(15000) if price_range != "0.00+" else 0.0
                            price_to = 10000000

                        fee = FeeModel(
                            auction="copart",
                            fee_type=fee_type,
                            amount=amount,
                            percent=is_percent,
                            price_from=price_from,
                            price_to=price_to,
                        )
                        db.add(fee)
                        logger.info(
                            f"Added fee: type={fee_type}, amount={amount}, percent={is_percent}, range={price_from}-{price_to}"
                        )

            # Commit changes to the database
            await db.commit()
            logger.info("Committed new fees for auction 'copart'")

            return {"status": "success", "count": len(fee_mappings)}

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching fees: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error in _update_car_fees_async: {e}", exc_info=True)
            await db.rollback()
            raise


# Celery task
@app.task(name="tasks.task.update_fees")
def update_car_fees():
    return anyio.run(_update_car_fees_async)
