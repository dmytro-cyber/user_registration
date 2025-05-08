from services.parsers.dc_scraper import DealerCenterScraper
from fastapi import APIRouter, Depends, Query
import asyncio
from core.dependencies import get_token
from schemas.schemas import DCResponseSchema
import logging
import json


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

router = APIRouter(prefix="/parsers", tags=["parsers"])


@router.get(
    "/scrape/dc",
    response_model=DCResponseSchema,
    description="Scrape data from Dealer Center",
)
async def scrape_dc(
    car_vin: str = Query(..., description="VIN of the car to scrape"),
):
    logger.info(f"Starting scrape for VIN {car_vin}")
    try:
        scraper = DealerCenterScraper(car_vin)
        result = await asyncio.to_thread(scraper.scrape)
        logger.info(f"Successfully scraped data for VIN {car_vin}")
        return DCResponseSchema(**result)
    except Exception as e:
        logger.error(f"Error during scraping for VIN {car_vin}: {str(e)}", exc_info=True)
        return DCResponseSchema(error=str(e))
