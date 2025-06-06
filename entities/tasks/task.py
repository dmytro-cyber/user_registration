import os
import logging
import httpx
import asyncio
from core.celery_config import app
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.future import select
from sqlalchemy import delete
from models.vehicle import CarModel, AutoCheckModel, FeeModel
from models.admin import ROIModel
from db.session import POSTGRESQL_DATABASE_URL
from core.config import settings
from storages import S3StorageClient
from datetime import datetime
import base64
from sqlalchemy.ext.asyncio import AsyncSession
from io import BytesIO

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Create async engine and session factory
engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
AsyncSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False)

# Initialize S3 storage client
s3_storage = S3StorageClient(
    endpoint_url=settings.S3_STORAGE_ENDPOINT,
    access_key=settings.S3_STORAGE_ACCESS_KEY,
    secret_key=settings.S3_STORAGE_SECRET_KEY,
    bucket_name=settings.S3_BUCKET_NAME,
)
logger.info("S3 storage client initialized successfully")

async def _parse_and_update_car_async(vin: str, car_name: str = None, car_engine: str = None):
    """
    Asynchronously parse and update car data from an external parser service.
    """
    logger.info(f"Starting _parse_and_update_car_async for VIN: {vin}, car_name: {car_name}, car_engine: {car_engine}")
    async with AsyncSessionFactory() as db:
        async with db.begin():
            try:
                url = f"http://parsers:8001/api/v1/parsers/scrape/dc?car_vin={vin}&car_name={car_name}&car_engine={car_engine}"
                headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
                logger.info(f"Prepared request URL: {url} with headers: {headers}")

                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=300.0, headers=headers)
                    logger.info(f"Received response with status: {response.status_code}")
                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" not in content_type:
                        logger.error(f"Expected application/json response, got {content_type}")
                        raise ValueError("Invalid content type")

                    data = response.json()

                    if data.get("error"):
                        logger.error(f"Errors in scraped data for VIN {vin}: {data.get('error')}")
                        raise ValueError(f"Scraping error: {data.get('error')}")

                    screenshot_data = None
                    if screenshot_data := data.get("screenshot"):
                        try:
                            screenshot_data = base64.b64decode(data["screenshot"])
                            logger.info(f"Decoded screenshot for VIN {vin}, size: {len(screenshot_data)} bytes")
                        except Exception as e:
                            logger.error(f"Failed to decode screenshot base64 for VIN {vin}: {str(e)}")
                            screenshot_data = None

                    logger.info(f"Querying database for car with VIN: {vin}")
                    query = select(CarModel).where(CarModel.vin == vin)
                    result = await db.execute(query)
                    car = result.scalars().first()

                    if not car:
                        logger.error(f"Car with VIN {vin} not found in database")
                        raise ValueError(f"Car with VIN {vin} not found")

                    logger.info(f"Found car with VIN {vin} in database, ID: {car.id}")

                    logger.info("Updating car data in database")
                    car.owners = data.get("owners")
                    logger.info(f"Updated owners: {car.owners}")

                    if data.get("mileage"):
                        car.has_correct_mileage = car.mileage == data.get("mileage")
                        logger.info(f"Updated has_correct_mileage: {car.has_correct_mileage}")

                    car.accident_count = data.get("accident_count")
                    logger.info(f"Updated accident_count: {car.accident_count}")

                    sum_price = 0
                    price = data.get("price", 0)
                    retail = data.get("retail", 0)
                    manheim = data.get("manheim", 0)
                    divisor = 0
                    if price or retail or manheim:
                        if price:
                            price = int(float(price))
                            sum_price += price
                            divisor += 1
                        if retail:
                            retail = int(float(retail))
                            sum_price += retail
                            divisor += 1
                        if manheim:
                            manheim = int(float(manheim))
                            sum_price += manheim
                            divisor += 1
                    car.avg_market_price = int(sum_price / divisor) if divisor > 0 else 0
                    query = select(ROIModel).order_by(ROIModel.created_at.desc())
                    result = await db.execute(query)
                    default_roi = result.scalars().first()
                    car.predicted_total_investment = (
                        (car.avg_market_price * 100) / (100 - default_roi.roi) if car.avg_market_price > 0 else 0
                    )
                    fees = await db.execute(select(FeeModel).where(FeeModel.auction == car.auction, FeeModel.price_from <= car.avg_market_price, FeeModel.price_to >= car.avg_market_price))
                    fees = fees.scalars().all()
                    car.auction_fee = sum(fee.amount for fee in fees)
                    car.suggested_bid = int(car.predicted_total_investment - car.auction_fee)
                    car.predicted_roi = default_roi.roi if car.total_investment > 0 else 0
                    logger.info(
                        f"Calculated avg_market_price: {car.avg_market_price}, total_investment: {car.total_investment}, roi: {car.roi}"
                    )
                    
                    car.predicted_profit_margin_percent = (
                        default_roi.profit_margin if car.predicted_total_investment > 0 else 0
                    )

                    if screenshot_data:
                        logger.info("Processing screenshot for upload to S3")
                        file_key = f"auto_checks/{vin}/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_screenshot.png"
                        screenshot_file = BytesIO(screenshot_data)
                        logger.info(
                            f"Attempting to upload to S3 with endpoint: {settings.S3_STORAGE_ENDPOINT}, bucket: {settings.S3_BUCKET_NAME}, file_key: {file_key}"
                        )
                        try:
                            await s3_storage.upload_fileobj(file_key, screenshot_file)
                            screenshot_url = f"{settings.S3_STORAGE_ENDPOINT}/{settings.S3_BUCKET_NAME}/{file_key}"
                            logger.info(f"Uploaded screenshot to S3, URL: {screenshot_url}")
                        except Exception as e:
                            logger.error(f"Failed to upload screenshot to S3 for VIN {vin}: {str(e)}")
                            raise

                        auto_check = AutoCheckModel(car_id=car.id, screenshot_url=screenshot_url)
                        db.add(auto_check)
                        logger.info(f"Added AutoCheckModel with screenshot URL for car ID: {car.id}")
                    else:
                        logger.warning(f"No screenshot found in response for VIN {vin}")

                    await db.commit()
                    logger.info(f"Successfully updated car VIN {vin} with scraped data and screenshot")
                    return {"status": "success", "vin": vin}

            except Exception as e:
                logger.error(f"Error in _parse_and_update_car_async for VIN {vin}: {str(e)}", exc_info=True)
                raise
            finally:
                logger.info("Closing database session")
                await db.close()

@app.task(name="tasks.task.parse_and_update_car")
def parse_and_update_car(vin: str, car_name: str = None, car_engine: str = None):
    """
    Sync task to parse and update car data.
    """
    logger.info(f"Starting Celery task parse_and_update_car for VIN: {vin}")
    return asyncio.run(_parse_and_update_car_async(vin, car_name, car_engine))

async def _update_car_bids_async():
    """
    Asynchronously update car bids by fetching cars with date < current time.
    """
    logger.info("Starting _update_car_bids_async")
    async with AsyncSessionFactory() as db:
        async with db.begin():
            try:
                current_time = datetime.utcnow()
                logger.info(f"Current time for filtering: {current_time}")
                query = select(CarModel.id, CarModel.link).where(CarModel.date < current_time)
                result = await db.execute(query)
                cars = [{"id": row.id, "link": row.link} for row in result.all()]
                logger.info(f"Found {len(cars)} cars with date < {current_time}")

                if not cars:
                    logger.info("No cars found to update bids")
                    return {"status": "success", "count": 0}

                url = "http://parsers:8001/api/v1/parsers/scrape/current_bid"
                headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
                payload = {"cars": cars}
                logger.info(f"Sending POST request to {url} with payload: {payload}")

                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=payload, timeout=300.0, headers=headers)
                    logger.info(f"Received response with status: {response.status_code}")
                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" not in content_type:
                        logger.error(f"Expected application/json response, got {content_type}")
                        raise ValueError("Invalid content type")

                    data = response.json()
                    logger.info(f"Received bid data: {data}")

                    for item in data:
                        car_id = item.get("id")
                        current_bid = item.get("current_bid")
                        if car_id and current_bid is not None:
                            query = select(CarModel).where(CarModel.id == car_id)
                            result = await db.execute(query)
                            car = result.scalars().first()
                            if car:
                                car.current_bid = int(float(current_bid))
                                logger.info(f"Updated current_bid to {current_bid} for car ID {car_id}")
                            else:
                                logger.error(f"Car with ID {car_id} not found for bid update")
                        else:
                            logger.warning(f"Invalid bid data for car ID {car_id}: {item}")

                    await db.commit()
                    logger.info(f"Successfully updated bids for {len(data)} cars")
                    return {"status": "success", "count": len(data)}

            except Exception as e:
                logger.error(f"Error in _update_car_bids_async: {str(e)}", exc_info=True)
                raise
            finally:
                logger.info("Closing database session")
                await db.close()

@app.task(name="tasks.task.update_car_bids")
def update_car_bids():
    """
    Sync task to update car bids.
    """
    logger.info("Starting Celery task update_car_bids")
    return asyncio.run(_update_car_bids_async())

async def _create_fee(db: AsyncSession, auction: str, fee_type: str, amount: float, price_range: str):
    """
    Helper function to create a FeeModel entry in the database.
    """
    price_from = price_range.split("-")[0] if "-" in price_range else 0
    price_to = price_range.split("-")[1] if "-" in price_range else None
    fee = FeeModel(
        auction=auction,
        fee_type=fee_type,
        amount=amount,
        price_from=price_from,
        price_to=price_to,
    )
    db.add(fee)
    await db.flush()

async def _update_fees_async():
    """
    Asynchronously update fees by fetching data from an external API.
    """
    logger.info("Starting _update_fees_async")
    async with AsyncSessionFactory() as db:
        async with db.begin():
            try:
                url = "http://parsers:8001/api/v1/parsers/scrape/fees"
                headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
                logger.info(f"Sending GET request to {url}")

                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=300.0, headers=headers)
                    logger.info(f"Received response with status: {response.status_code}")
                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" not in content_type:
                        logger.error(f"Expected application/json response, got {content_type}")
                        raise ValueError("Invalid content type")

                    data = response.json()
                    if data[0].get("source") == "copart":
                        copart_fees = data[0]["fees"]
                        iaai_fees = data[1]["fees"]
                    else:
                        copart_fees = data[1]["fees"]
                        iaai_fees = data[0]["fees"]

                    logger.info("Deleting existing copart fees")
                    await db.execute(delete(FeeModel).where(FeeModel.auction == "copart"))
                    await db.flush()

                    secured = copart_fees.get("bidding_fees").get("secured").get("secured")
                    for price, fee in secured.items():
                        await _create_fee(db, "copart", "secured", fee, price)

                    gate_fee = copart_fees.get("gate_fee").get("amount")
                    await _create_fee(db, "copart", "gate_fee", gate_fee, "0")

                    virtual_fee = copart_fees.get("virtual_fee").get("live_bid")
                    for price, fee in virtual_fee.items():
                        await _create_fee(db, "copart", "virtual_fee", fee, price)

                    environmental_fee = copart_fees.get("environmental_fee").get("amount")
                    await _create_fee(db, "copart", "environmental_fee", environmental_fee, "0")

                    logger.info("Deleting existing iaai fees")
                    await db.execute(delete(FeeModel).where(FeeModel.auction == "iaai"))
                    await db.flush()

                    standard_volume_buyer_fee = iaai_fees.get("standard_volume_buyer_fee").get("fees")
                    for price, fee in standard_volume_buyer_fee.items():
                        await _create_fee(db, "iaai", "standard_volume_buyer_fee", fee, price)

                    live_online_bid_fee = iaai_fees.get("live_online_bid_fee").get("fees")
                    for price, fee in live_online_bid_fee.items():
                        await _create_fee(db, "iaai", "live_online_bid_fee", fee, price)

                    service_fee = iaai_fees.get("service_fee").get("amount")
                    await _create_fee(db, "iaai", "service_fee", service_fee, "0")

                    environmental_fee = iaai_fees.get("environmental_fee").get("amount")
                    await _create_fee(db, "iaai", "environmental_fee", environmental_fee, "0")

                    title_handling_fee = iaai_fees.get("title_handling_fee").get("amount")
                    await _create_fee(db, "iaai", "title_handling_fee", title_handling_fee, "0")

                    logger.info(f"Received and processed fee data: {data}")
                    logger.info("Successfully updated fees")
                    return {"status": "success"}

            except Exception as e:
                logger.error(f"Error in _update_fees_async: {str(e)}", exc_info=True)
                raise
            finally:
                logger.info("Closing database session")
                await db.close()

@app.task(name="tasks.task.update_fees")
def update_fees():
    """
    Sync task to update fees.
    """
    logger.info("Starting Celery task update_fees")
    return asyncio.run(_update_fees_async())