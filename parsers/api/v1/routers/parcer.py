from services.parsers.dc_scraper import DealerCenterScraper
from services.parsers.copart_current_bid_parser import get_current_bid
from services.fees.copart_fees_parser import scrape_copart_fees
from services.fees.iaai_fees_parser import scrape_iaai_fees
from fastapi import APIRouter, Depends, Query
import asyncio
from core.dependencies import get_token
from schemas.schemas import (
    DCResponseSchema,
    UpdateCurrentBidRequestSchema,
    UpdateCurrentBidResponseSchema,
    UpdateCurrentBidListRequestSchema,
    UpdateCurrentBidListResponseSchema,
)
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


@router.post(
    "/scrape/current_bid",
    response_model=UpdateCurrentBidListResponseSchema,
)
async def scrape_current_bid(
    data: UpdateCurrentBidListRequestSchema,
) -> UpdateCurrentBidListResponseSchema:
    """
    Scrape current bid data from Copart for a list of URLs.
    """
    logger.info(f"Starting scrape for current bid with data length: {len(data)}")
    data = [item.dict() for item in data]
    respose = get_current_bid(data)
    logger.info(f"Successfully scraped current bid, data length: {len(respose)}")
    return respose


@router.get(
    "scrape/fees",
)
async def scrape_fees():
    """
    Scrape fees from Copart.
    """
    logger.info("Starting scrape for fees")
    try:
        # Placeholder for actual fee scraping logic
        copart_fees = await scrape_copart_fees()
        iaai_fees = await scrape_iaai_fees()
        fees = {"copart": copart_fees, "iaai": iaai_fees}  # Example data
        logger.info("Successfully scraped fees")
        return {"fees": fees}
    except Exception as e:
        logger.error(f"Error during fee scraping: {str(e)}", exc_info=True)
        return {"error": str(e)}
