import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from core.config import settings
from core.dependencies import get_token
from schemas.schemas import (
    DCResponseSchema,
    UpdateCurrentBidListRequestSchema,
    UpdateCurrentBidListResponseSchema,
    UpdateCurrentBidRequestSchema,
    UpdateCurrentBidResponseSchema,
)
from services.fees.copart_fees_parser import scrape_copart_fees
from services.fees.iaai_fees_image_parser import parse_fee_table
from services.parsers.copart_current_bid_parser import get_current_bid

if settings.ENVIRON == "dev":
    from services.parsers.dc_scraper_local import DealerCenterScraper
else:
    from services.parsers.dc_scraper import DealerCenterScraper


router = APIRouter()
executor = ThreadPoolExecutor()


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
    car_name: str = Query(None, description="Name of the car (optional)"),
    car_engine: str = Query(None, description="Engine type of the car (optional)"),
    car_mileage: int = Query(None, description="Mileage of the car (optional)"),
    car_make: str = Query(..., description="Make of the car"),
    car_model: str = Query(..., description="Model of the car"),
    car_year: int = Query(..., description="Year of the car"),
    car_transmison: str = Query(..., description="Transmission type of the car"),
    only_history: bool = Query(False, description="If true, only scrape history data"),
):
    attempts = 0
    max_attempts = 3
    retry_delay = 2

    while attempts < max_attempts:
        logger.info(f"Starting scrape for VIN {car_vin}, attempt {attempts + 1}/{max_attempts}")
        try:
            scraper = DealerCenterScraper(
                vin=car_vin,
                vehicle_name=car_name,
                engine=car_engine,
                make=car_make,
                model=car_model,
                year=car_year,
                transmission=car_transmison,
                odometer=car_mileage,
            )
            if only_history:
                result = await scraper.get_history_only_async()
            else:
                result = await scraper.get_history_and_market_data_async()
            logger.info(f"Successfully scraped data for VIN {car_vin}")
            return DCResponseSchema(**result)
        except Exception as e:
            logger.error(f"Error during scraping for VIN {car_vin}: {str(e)} attempt: {attempts + 1}", exc_info=True)
            attempts += 1
            if attempts < max_attempts:
                await asyncio.sleep(retry_delay)
                continue
            else:
                logger.error(f"Failed to scrape after {max_attempts} attempts for VIN {car_vin}")
                return DCResponseSchema(error=str(e))

    logger.error(f"Unexpected exit after {max_attempts} attempts for VIN {car_vin}")
    return DCResponseSchema(error="Unexpected error during scraping")


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
    logger.info(f"Starting scrape for current bid with data length: {len(data.items)} ")
    respose = await get_current_bid(data.items)
    # logger.info(f"Successfully scraped current bid, data length: {len(respose)}")
    # logger.info(f"Response: {json.dumps(respose, indent=2)}")
    return {"bids": respose}


@router.get(
    "/scrape/fees",
)
async def scrape_fees():
    """
    Scrape fees from Copart.
    """
    logger.info("Starting scrape for fees")
    try:
        # Placeholder for actual fee scraping logic
        loop = asyncio.get_running_loop()
        copart_fees = await loop.run_in_executor(executor, scrape_copart_fees)
        # iaai_fees = await loop.run_in_executor(executor, scrape_iaai_fees)
        fees = {"copart": copart_fees}  # Example data
        logger.info("Successfully scraped fees")
        return {"fees": fees}
    except Exception as e:
        logger.error(f"Error during fee scraping: {str(e)}", exc_info=True)
        return {"error": str(e)}


@router.post("/scrape/iaai/fees")
async def parse_fees(high_volume: UploadFile = File(...), internet_bid: UploadFile = File(...)):
    """Parse fees from two uploaded images (SVG or PNG). Internet file -> use 'Live Bid' column."""
    try:
        logger.info(f"Received files: high_volume={high_volume.filename}, internet_bid={internet_bid.filename}")
        expected_extensions = {".png", ".svg"}

        files = {"high_volume": high_volume, "internet_bid": internet_bid}
        saved_paths = {}
        for name, file in files.items():
            filename = file.filename
            _, ext = os.path.splitext(filename.lower())
            if ext not in expected_extensions:
                raise HTTPException(status_code=400, detail=f"File {filename} must be .png or .svg")
            file_path = f"/tmp/{name}_{filename}"
            with open(file_path, "wb") as buffer:
                buffer.write(await file.read())
            saved_paths[name] = file_path

        # Parse:
        #  - High volume: “звичайний” останній стовпчик (або той, що позначений як PRICE/BID -> fee = right-most)
        hv_fees = parse_fee_table(saved_paths["high_volume"])
        #  - Internet:  беремо саме колонку з хедером "LIVE BID"
        internet_live_fees = parse_fee_table(saved_paths["internet_bid"], target_fee_header="LIVE BID")

        fees = {
            # стабільні ключі для backend
            "high_volume_buyer_fees": {
                "fees": hv_fees,
                "currency": "USD",
                "description": "Parsed fees for High Volume section",
            },
            "internet_bid_buyer_fees": {
                "fees": internet_live_fees,
                "currency": "USD",
                "description": "Parsed fees for Internet Live Bid section",
            },
            # фіксовані комісії — залишив як у тебе
            "service_fee": {"amount": 95.0, "currency": "USD", "description": "Per unit for vehicle handling"},
            "environmental_fee": {"amount": 15.0, "currency": "USD", "description": "Per unit for environmental regulations"},
            "title_handling_fee": {"amount": 20.0, "currency": "USD", "description": "Applied to all purchases"},
        }

        # cleanup
        for path in saved_paths.values():
            try:
                os.remove(path)
            except OSError:
                pass

        result = {
            "source": "iaai",
            "payment_method": "standard",
            "fees": fees,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return JSONResponse(status_code=200, content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error processing files")
        raise HTTPException(status_code=500, detail=f"Error processing files: {str(e)}")
