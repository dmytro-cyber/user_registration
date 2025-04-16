import logging
import httpx
import asyncio
from core.celery_config import app
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.vehicle import CarModel
from schemas.vehicle import CarBaseSchema
from db.session import get_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def fetch_data_from_api() -> list[dict]:
    """
    Fetch data from an external API.
    Since the API is not specified, returning mock data as a placeholder.
    """
    try:
        async with httpx.AsyncClient() as client:
            # response = await client.get("https://api.example.com/data")
            # response.raise_for_status()
            # return response.json()
            
            # Mock data
            return [
                {"name": "Example 1", "description": "Description for Example 1"},
                {"name": "Example 2", "description": "Description for Example 2"},
            ]
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error while fetching data from API: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error fetching data from API: {str(e)}")
        raise

async def save_data_to_db(db: AsyncSession, data: list[dict]):
    """
    Save data to the database.
    """
    try:
        for item in data:
            schema_item = CarBaseSchema(**item)
            
            existing_item = await db.execute(
                select(CarModel).where(CarModel.name == schema_item.name)
            )
            existing_item = existing_item.scalars().first()

            if existing_item:
                existing_item.description = schema_item.description
                db.add(existing_item)
                logger.debug(f"Updated existing item: {schema_item.name}")
            else:
                new_item = CarModel(
                    name=schema_item.name,
                    description=schema_item.description
                )
                db.add(new_item)
                logger.debug(f"Added new item: {schema_item.name}")

        await db.commit()
        logger.info(f"Successfully saved {len(data)} items to the database")

    except Exception as e:
        await db.rollback()
        logger.error(f"Error saving data to database: {str(e)}")
        raise

@app.task
async def fetch_and_save_data():
    """
    Periodic task: fetches data from API and saves it to the database.
    """
    logger.info("Starting periodic task to fetch and save data")
    
    try:
        # Since Celery doesn't support async directly, use asyncio.run
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Create a new database session
        db = get_db()
        # Fetch data from API
        data = await fetch_data_from_api()
        logger.info(f"Fetched {len(data)} items from API")
        # Save data to database
        await save_data_to_db(db, data)

    except Exception as e:
        logger.error(f"Periodic task failed: {str(e)}")
        raise