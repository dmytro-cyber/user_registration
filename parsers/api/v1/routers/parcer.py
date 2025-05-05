from services.parsers.dc_scraper import DealerCenterScraper
from fastapi import APIRouter, Depends, Query
import asyncio
from core.dependencies import get_token
from schemas.schemas import DCResponseSchema

router = APIRouter(prefix="/parsers", tags=["parsers"])


@router.get(
    "/scrape/dc",
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
        result = await asyncio.to_thread(scraper.scrape)
        from starlette.responses import Response
        from multipart import MultipartEncoder

        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        encoder = MultipartEncoder(
            fields={
                "data": ("data", result["data"], "application/json"),
                "screenshot": ("screenshot.png", result["screenshot"], "image/png")
            },
            boundary=boundary
        )

        return Response(
            content=encoder.to_string(),
            media_type=f"multipart/form-data; boundary={boundary}"
    )
    except Exception as e:
        return {"error": str(e)}
