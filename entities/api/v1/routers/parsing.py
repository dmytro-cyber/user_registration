"""Compatibility routes for the former parsers application."""

from urllib.parse import urlencode

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from integrations.apicar.client import ApicarVehicleNotFound, get_vehicle_async
from integrations.parsing import ParsingClient, start_import
from schemas.parsing import DCResponseSchema, UpdateCurrentBidListRequestSchema, UpdateCurrentBidListResponseSchema

router = APIRouter(prefix="/parsers", tags=["parsers"])
apicar_router = APIRouter(tags=["apicar"])
startup_router = APIRouter()


def _body(response):
    if response.status_code >= 400:
        try:
            detail = response.json()["detail"]
        except (ValueError, KeyError):
            detail = response.text
        raise HTTPException(response.status_code, detail)
    return response.json()


@router.get("/scrape/dc", response_model=DCResponseSchema, description="Scrape data from Dealer Center")
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
    values = dict(
        car_vin=car_vin,
        car_name=car_name,
        car_engine=car_engine,
        car_mileage=car_mileage,
        car_make=car_make,
        car_model=car_model,
        car_year=car_year,
        car_transmison=car_transmison,
        only_history=only_history,
    )
    query = urlencode({key: value for key, value in values.items() if value is not None})
    async with ParsingClient(timeout=340) as client:
        return _body(await client.get(f"/api/v1/parsers/scrape/dc?{query}"))


@router.post("/scrape/current_bid", response_model=UpdateCurrentBidListResponseSchema)
async def scrape_current_bid(data: UpdateCurrentBidListRequestSchema) -> UpdateCurrentBidListResponseSchema:
    """Scrape current bid data from Copart for a list of URLs."""
    async with ParsingClient() as client:
        return _body(await client.post("/api/v1/parsers/scrape/current_bid", json=data.model_dump()))


@router.get("/scrape/fees")
async def scrape_fees():
    """Scrape fees from Copart."""
    async with ParsingClient(timeout=300) as client:
        return _body(await client.get("/api/v1/parsers/scrape/fees"))


@router.post("/scrape/iaai/fees")
async def parse_fees(high_volume: UploadFile = File(...), internet_bid: UploadFile = File(...)):
    """Parse fees from two uploaded images (SVG or PNG). Internet file -> use 'Live Bid' column."""
    files = {
        "high_volume": (high_volume.filename, await high_volume.read(), high_volume.content_type),
        "internet_bid": (internet_bid.filename, await internet_bid.read(), internet_bid.content_type),
    }
    async with ParsingClient(timeout=300) as client:
        return _body(await client.post("/api/v1/parsers/scrape/iaai/fees", files=files))


@apicar_router.get("/apicar/get/{car_vin}", description="Get data from apicar")
async def get_sales_history(car_vin: str):
    """Get data from apicar."""
    return await get_vehicle_async(car_vin)


@apicar_router.get("/apicar/update/{car_vin}", description="Get data from apicar")
async def get_update(car_vin: str):
    """Get data from apicar."""
    try:
        return await get_vehicle_async(car_vin, update=True)
    except ApicarVehicleNotFound:
        raise HTTPException(404, detail=f"VIN {car_vin} not found")


@startup_router.post("/startup")
def startup():
    start_import()
