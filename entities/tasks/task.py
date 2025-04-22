# import os
# import logging
# import asyncio
# import httpx
# from core.celery_config import app as celery_app
# from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
# from sqlalchemy.orm import sessionmaker
# from sqlalchemy.future import select
# from models.vehicle import CarModel  # Імпортуй свою модель CarModel

# logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# DATABASE_URL = "postgresql+asyncpg://user:password@db:5432/dbname"  # Онови відповідно до твоїх налаштувань
# engine = create_async_engine(DATABASE_URL, echo=True)
# AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


# async def parse_and_update_car_async(car_id: int, vin: str):

#     async with AsyncSessionLocal() as db:
#         try:
#             url = f"http://parsers:8000/api/v1/parsers/scrape/dc/{vin}"
#             async with httpx.AsyncClient() as client:
#                 response = await client.get(url, timeout=300.0, headers={"api-key": os.getenv("APICAR_KEY")})
#                 response.raise_for_status()
#                 data = response.json()
#                 logging.info(f"Scraped data for VIN {vin}: {data}")

#             query = select(CarModel).where(CarModel.id == car_id)
#             result = await db.execute(query)
#             car = result.scalars().first()

#             if not car:
#                 logging.error(f"Car with ID {car_id} not found in database")
#                 return

#             car.owners = data.get("owners")

#             if data.get("mileage"):
#                 car.has_correct_mileage = car.mileage == data.get("mileage")
            
#             car.accident_count = data.get("accident_count")
            

#             await db.commit()
#             logging.info(f"Successfully updated car ID {car_id} with scraped data")

#         except Exception as e:
#             logging.error(f"Error in async task for car ID {car_id}: {str(e)}")
#             await db.rollback()

# @celery_app.task(name="main.parse_and_update_car")
# def parse_and_update_car(car_id: int, vin: str):
#     try:
#         asyncio.run(parse_and_update_car_async(car_id, vin))
#     except Exception as e:
#         logging.error(f"Error in Celery task for car ID {car_id}: {str(e)}")