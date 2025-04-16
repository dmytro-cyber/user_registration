from services.parsers.dc_scraper import DealerCenterScraper
from fastapi import APIRouter, Depends, Query
import asyncio
from core.dependencies import get_token

from schemas.schemas import DCResponseSchema


router = APIRouter(prefix="/parsers", tags=["parsers"])


@router.get(
    "/scrape/dc/{car_vin}",
    response_model=DCResponseSchema,
    description="Scrape data from Dealer Center",
)
async def scrape_dc(car_vin: str):
    """
    Scrape data from Dealer Center.
    """
    # , token: str = Depends(get_token)
    try:
        scraper = DealerCenterScraper(car_vin)
        data = await asyncio.to_thread(scraper.scrape)
        return DCResponseSchema(**data)
    except Exception as e:
        return {"error": str(e)}
