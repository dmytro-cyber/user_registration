from services.parsers.dc_scraper import DealerCenterScraper
from fastapi import APIRouter, Depends, Query
import asyncio
from core.dependencies import get_token
from schemas.schemas import DCResponseSchema

router = APIRouter(prefix="/parsers", tags=["parsers"])


@router.get(
    "/scrape/dc",  # Видаляємо {car_vin} зі шляху
    response_model=DCResponseSchema,
    description="Scrape data from Dealer Center",
)
async def scrape_dc(
    car_vin: str = Query(str, description="VIN of the car to scrape"),
    car_name: str = Query(None, description="Name of the car"),
    car_year: int = Query(None, description="Year of the car"),
):
    """
    Scrape data from Dealer Center.

    Args:
        car_vin (str): VIN of the car to scrape, provided as a query parameter.
    """
    # , token: str = Depends(get_token)
    try:
        scraper = DealerCenterScraper(car_vin, car_name, car_year)
        data = await asyncio.to_thread(scraper.scrape)
        return DCResponseSchema(**data)
    except Exception as e:
        return {"error": str(e)}
