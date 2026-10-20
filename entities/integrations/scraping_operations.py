"""Heavy dependencies are imported only inside the serialized scraping worker."""

import asyncio
import base64
import os
import tempfile
from datetime import datetime
from pathlib import Path


async def execute_operation(operation, payload):
    if operation == "dealercenter":
        from schemas.parsing import DCResponseSchema

        if os.getenv("PARSING_ENVIRON", "prod") == "dev":
            from integrations.dealercenter.local_scraper import DealerCenterScraper
        else:
            from integrations.dealercenter.scraper import DealerCenterScraper
        error = "Unexpected error during scraping"
        for attempt in range(3):
            try:
                scraper = DealerCenterScraper(
                    vin=payload["car_vin"],
                    vehicle_name=payload.get("car_name"),
                    engine=payload.get("car_engine"),
                    make=payload["car_make"],
                    model=payload["car_model"],
                    year=payload["car_year"],
                    transmission=payload["car_transmison"],
                    odometer=payload.get("car_mileage"),
                )
                result = await (
                    scraper.get_history_only_async()
                    if payload.get("only_history", False)
                    else scraper.get_history_and_market_data_async()
                )
                return {"status": 200, "body": DCResponseSchema(**result).model_dump()}
            except Exception as exc:
                error = str(exc)
                if attempt < 2:
                    await asyncio.sleep(2)
        return {"status": 200, "body": DCResponseSchema(error=error).model_dump()}

    if operation == "copart_fees":
        try:
            from integrations.fees.copart import scrape_copart_fees

            return {"status": 200, "body": {"fees": {"copart": scrape_copart_fees()}}}
        except Exception as exc:
            return {"status": 200, "body": {"error": str(exc)}}

    if operation == "iaai_fees":
        try:
            from integrations.fees.iaai_image import parse_fee_table

            with tempfile.TemporaryDirectory(prefix="iaai-fees-") as directory:
                paths = {}
                for name in ("high_volume", "internet_bid"):
                    file = payload[name]
                    extension = Path(file["filename"].lower()).suffix
                    if extension not in {".png", ".svg"}:
                        return {"status": 400, "body": {"detail": f"File {file['filename']} must be .png or .svg"}}
                    path = Path(directory) / f"{name}{extension}"
                    path.write_bytes(base64.b64decode(file["content"]))
                    paths[name] = str(path)
                high_volume = parse_fee_table(paths["high_volume"])
                internet = parse_fee_table(paths["internet_bid"], target_fee_header="LIVE BID")
            return {
                "status": 200,
                "body": {
                    "source": "iaai",
                    "payment_method": "standard",
                    "fees": {
                        "high_volume_buyer_fees": {
                            "fees": high_volume,
                            "currency": "USD",
                            "description": "Parsed fees for High Volume section",
                        },
                        "internet_bid_buyer_fees": {
                            "fees": internet,
                            "currency": "USD",
                            "description": "Parsed fees for Internet Live Bid section",
                        },
                        "service_fee": {
                            "amount": 95.0,
                            "currency": "USD",
                            "description": "Per unit for vehicle handling",
                        },
                        "environmental_fee": {
                            "amount": 15.0,
                            "currency": "USD",
                            "description": "Per unit for environmental regulations",
                        },
                        "title_handling_fee": {
                            "amount": 20.0,
                            "currency": "USD",
                            "description": "Applied to all purchases",
                        },
                    },
                    "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            }
        except Exception as exc:
            return {"status": 500, "body": {"detail": f"Error processing files: {exc}"}}
    raise ValueError(f"Unsupported scraping operation: {operation}")
