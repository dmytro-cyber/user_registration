import os
import logging
import asyncio
import httpx
from core.celery_config import app
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from models.vehicle import CarModel
from models.vehicle import AutoCheckModel
from db.session import POSTGRESQL_DATABASE_URL
from core.config import settings
from storages import S3StorageClient
from datetime import datetime
import base64
from io import BytesIO

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

s3_storage = S3StorageClient(
    endpoint_url=settings.S3_STORAGE_ENDPOINT,
    access_key=settings.S3_STORAGE_ACCESS_KEY,
    secret_key=settings.S3_STORAGE_SECRET_KEY,
    bucket_name=settings.S3_BUCKET_NAME
)
logger.info("S3 storage client initialized successfully")

async def parse_and_update_car_async(vin: str, car_name: str = None, car_engine: str = None):
    logger.info(f"Starting parse_and_update_car_async for VIN: {vin}, car_name: {car_name}, car_engine: {car_engine}")
    async with AsyncSessionLocal() as db:
        try:
            # Формуємо URL для запиту
            url = f"http://parsers:8001/api/v1/parsers/scrape/dc?car_vin={vin}&car_name={car_name}&car_engine={car_engine}"
            headers = {"X-Auth-Token": settings.PARSERS_AUTH_TOKEN}
            logger.info(f"Prepared request URL: {url} with headers: {headers}")

            # Виконуємо запит до парсера
            logger.info("Sending request to parser service")
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=300.0, headers=headers)
                logger.info(f"Received response with status: {response.status_code}")
                response.raise_for_status()

                # Перевіряємо, що відповідь у форматі JSON
                content_type = response.headers.get("Content-Type", "")
                logger.info(f"Response Content-Type: {content_type}")
                if "application/json" not in content_type:
                    logger.error(f"Expected application/json response, got {content_type}")
                    return

                # Парсимо JSON-відповідь
                data = response.json()
                logger.info(f"""
                            aaaaaaaaaaaaaaaaaaaaaaaaaaaaa
                            {data.get('owners')}
                            {data.get('accident_count')}
                            {data.get('mileage')}
                            {data.keys()}
                            """)

                if data.get("error"):
                    logger.error(f"Errors in scraped data for VIN {vin}: {data.get('error')}")
                    return

                # Декодуємо скріншот із base64, якщо він є
                screenshot_data = None
                if screenshot_data := data.get("screenshot"):
                    try:
                        screenshot_data = base64.b64decode(data["screenshot"])
                        logger.info(f"Decoded screenshot for VIN {vin}, size: {len(screenshot_data)} bytes")
                    except Exception as e:
                        logger.error(f"Failed to decode screenshot base64 for VIN {vin}: {str(e)}")
                        screenshot_data = None

                # Знаходимо автомобіль у базі даних
                logger.info(f"Querying database for car with VIN: {vin}")
                query = select(CarModel).where(CarModel.vin == vin)
                result = await db.execute(query)
                car = result.scalars().first()

                if not car:
                    logger.error(f"Car with VIN {vin} not found in database")
                    return

                logger.info(f"Found car with VIN {vin} in database, ID: {car.id}")

                # Оновлення основних даних автомобіля
                logger.info("Updating car data in database")
                car.owners = data.get("owners")
                logger.info(f"Updated owners: {car.owners}")

                if data.get("mileage"):
                    car.has_correct_mileage = car.mileage == data.get("mileage")
                    logger.info(f"Updated has_correct_mileage: {car.has_correct_mileage}")

                car.accident_count = data.get("accident_count")
                logger.info(f"Updated accident_count: {car.accident_count}")

                # Обчислення sum_price з обробкою None
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
                car.total_investment = car.avg_market_price / 1.8 if car.avg_market_price > 0 else 0
                car.roi = (car.avg_market_price - car.total_investment) / car.total_investment * 100 if car.total_investment > 0 else 0
                logger.info(f"Calculated avg_market_price: {car.avg_market_price}, total_investment: {car.total_investment}, roi: {car.roi}")

                if screenshot_data:
                    logger.info("Processing screenshot for upload to S3")
                    file_key = f"auto_checks/{vin}/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_screenshot.png"
                    screenshot_file = BytesIO(screenshot_data)
                    logger.info(f"Attempting to upload to S3 with endpoint: {settings.S3_STORAGE_ENDPOINT}, bucket: {settings.S3_BUCKET_NAME}, file_key: {file_key}")
                    try:
                        await s3_storage.upload_fileobj(file_key, screenshot_file)  # Асинхронний виклик
                        screenshot_url = f"{settings.S3_STORAGE_ENDPOINT}/{settings.S3_BUCKET_NAME}/{file_key}"
                        logger.info(f"Uploaded screenshot to S3, URL: {screenshot_url}")
                    except Exception as e:
                        logger.error(f"Failed to upload screenshot to S3 for VIN {vin}: {str(e)}")
                        raise

                    auto_check = AutoCheckModel(
                        car_id=car.id,
                        screenshot_url=screenshot_url
                    )
                    db.add(auto_check)
                    logger.info(f"Added AutoCheckModel with screenshot URL for car ID: {car.id}")
                else:
                    logger.warning(f"No screenshot found in response for VIN {vin}")

                logger.info("Committing changes to database")
                await db.commit()
                logger.info(f"Successfully updated car VIN {vin} with scraped data and screenshot")

        except Exception as e:
            logger.error(f"Error in async task for car VIN {vin}: {str(e)}", exc_info=True)
            await db.rollback()
            logger.info("Database transaction rolled back due to error")
        finally:
            logger.info("Closing database session")
            await db.close()

@app.task(name="tasks.task.parse_and_update_car")
def parse_and_update_car(vin: str):
    logger.info(f"Starting Celery task parse_and_update_car for VIN: {vin}")
    try:
        asyncio.run(parse_and_update_car_async(vin))
        logger.info(f"Completed Celery task for VIN {vin}")
    except Exception as e:
        logger.error(f"Error in Celery task for car VIN {vin}: {str(e)}", exc_info=True)