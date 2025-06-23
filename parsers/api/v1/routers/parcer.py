from services.parsers.dc_scraper import DealerCenterScraper
from services.parsers.copart_current_bid_parser import get_current_bid
from services.fees.copart_fees_parser import scrape_copart_fees
from services.fees.iaai_fees_parser import scrape_iaai_fees
from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
import os
from datetime import datetime
import asyncio
from services.fees.iaai_fees_image_parser import parse_svg_table
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
from concurrent.futures import ThreadPoolExecutor

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
    only_history: bool = Query(False, description="If true, only scrape history data"),
):
    logger.info(f"Starting scrape for VIN {car_vin}")
    if only_history:
        try:
            scraper = DealerCenterScraper(vin=car_vin, vehicle_name=car_name, engine=car_engine)
            result = await asyncio.to_thread(scraper.scrape_only_history)
            logger.info(f"Successfully scraped data for VIN {car_vin}")
            return DCResponseSchema(**result)
        except Exception as e:
            logger.error(f"Error during scraping for VIN {car_vin}: {str(e)}", exc_info=True)
            return DCResponseSchema(error=str(e))
    else:
        try:
            scraper = DealerCenterScraper(vin=car_vin, vehicle_name=car_name, engine=car_engine)
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
    logger.info(f"Starting scrape for current bid with data length: {len(data.items)} ")
    respose = await get_current_bid(data.items)
    logger.info(f"Successfully scraped current bid, data length: {len(respose)}")
    return respose


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
async def parse_fees(
    high_volume: UploadFile = File(...),
    internet_bid: UploadFile = File(...)
):
    """Endpoint to parse fees from three uploaded images (SVG or PNG)."""
    try:
        # Define expected file extensions
        expected_extensions = {".png", ".svg"}

        # Save uploaded files
        files = {
            "high_volume": high_volume,
            "internet_bid": internet_bid
        }

        saved_paths = {}
        for name, file in files.items():
            # Check file extension
            filename = file.filename
            _, ext = os.path.splitext(filename.lower())
            if ext not in expected_extensions:
                raise HTTPException(status_code=400, detail=f"File {filename} must be .png or .svg")

            # Save file temporarily
            file_path = f"/tmp/{name}_{filename}"
            with open(file_path, "wb") as buffer:
                buffer.write(await file.read())
            saved_paths[name] = file_path
            logger.info(f"Saved uploaded file as {file_path}")

        # Parse each image
        fees = {}
        for name, path in saved_paths.items():
            parsed_fees = parse_svg_table(path)
            if parsed_fees:
                fees[f"{name}_buyer_fees"] = {
                    "fees": parsed_fees,
                    "currency": "USD",
                    "description": f"Parsed fees for {name.replace('_', ' ').title()} section"
                }
                logger.info(f"Parsed {name} fees: {parsed_fees}")
            else:
                logger.warning(f"No fees parsed from {name} image.")

        # Add fixed fees (static as per original logic)
        fees.update({
            "service_fee": {"amount": 95.0, "currency": "USD", "description": "Per unit for vehicle handling"},
            "environmental_fee": {"amount": 15.0, "currency": "USD", "description": "Per unit for environmental regulations"},
            "title_handling_fee": {"amount": 20.0, "currency": "USD", "description": "Applied to all purchases"}
        })
        logger.info("Added fixed fees: Service Fee, Environmental Fee, Title Handling Fee")

        result = {
            "source": "iaai",
            "payment_method": "standard",
            "fees": fees,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Clean up temporary files
        for path in saved_paths.values():
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"Removed temporary file: {path}")

        return result

    except Exception as e:
        logger.error(f"Error processing files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing files: {str(e)}")
