import os
import logging
import asyncio
import httpx
from core.celery_config import app
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from models.vehicle import CarModel
from db.session import POSTGRESQL_DATABASE_URL
from core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

engine = create_async_engine(POSTGRESQL_DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def parse_and_update_car_async(vin: str, car_name: str = None, car_engine: str = None):

    async with AsyncSessionLocal() as db:
        try:
            url = f"http://parsers:8001/api/v1/parsers/scrape/dc?car_vin={vin}&car_name={car_name}&car_engine={car_engine}"
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=300.0, headers={"X-Auth-Token": settings.PARSERS_AUTH_TOKEN})
                response.raise_for_status()
                data = response.json()
                logging.info(f"Scraped data for VIN {vin}: {data}")

            query = select(CarModel).where(CarModel.vin == vin)
            result = await db.execute(query)
            car = result.scalars().first()

            if not car:
                logging.error(f"Car with VIN {vin} not found in database")
                return

            if data.get("error"):
                logging.error(f"Errors in scraped data for VIN {vin}: {data.get('errors')}")
                return

            car.owners = data.get("owners")

            if data.get("mileage"):
                car.has_correct_mileage = car.mileage == data.get("mileage")

            car.accident_count = data.get("accident_count")
            
            sum_price = data.get("price", 0) + data.get("retail", 0) + data.get("manheim", 0)
            divisor = 0
            if sum_price > 0:
                if data.get("price", 0):
                    divisor += 1
                if data.get("retail", 0):
                    divisor += 1
                if data.get("manheim", 0):
                    divisor += 1
            car.avg_market_price = int(sum_price / divisor)
            car.total_investment = car.avg_market_price / 1.8
            car.roi = (car.avg_market_price - car.total_investment) / car.total_investment * 100

            await db.commit()
            logging.info(f"Successfully updated car VIN {vin} with scraped data")

        except Exception as e:
            logging.error(f"Error in async task for car VIN {vin}: {str(e)}")
            await db.rollback()
        finally:
            await db.close()


@app.task(name="tasks.task.parse_and_update_car")
def parse_and_update_car(vin: str, car_name: str = None, car_engine: str = None):
    try:
        asyncio.run(parse_and_update_car_async(vin))
    except Exception as e:
        logging.error(f"Error in Celery task for car VIN {vin}: {str(e)}")
