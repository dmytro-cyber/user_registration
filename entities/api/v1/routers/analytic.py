from fastapi import APIRouter, Depends, HTTPException, status, Query, File, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.encoders import jsonable_encoder
from db.session import get_db
from typing import Optional, List, Literal
from sqlalchemy import text
from datetime import date, datetime
import logging
import logging.handlers
import os

# Configure logging for production environment
logger = logging.getLogger("admin_router")
logger.setLevel(logging.DEBUG)  # Set the default logging level

# Define formatter for structured logging
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - [RequestID: %(request_id)s] - [UserID: %(user_id)s] - %(message)s"
)

# Comment out file logging setup to disable writing to file
# log_directory = "logs"
# if not os.path.exists(log_directory):
#     os.makedirs(log_directory)
# file_handler = logging.handlers.RotatingFileHandler(
#     filename="logs/admin.log",
#     maxBytes=10 * 1024 * 1024,  # 10 MB
#     backupCount=5,  # Keep up to 5 backup files
# )
# file_handler.setFormatter(formatter)
# file_handler.setLevel(logging.DEBUG)

# Set up console handler for debug output
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# Add handlers to the logger (only console handler is active)
# logger.addHandler(file_handler)  # Comment out to disable file logging
logger.addHandler(console_handler)

# Custom filter to add context (RequestID, UserID)
class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, "request_id", "N/A")
        record.user_id = getattr(record, "user_id", "N/A")
        return True

logger.addFilter(ContextFilter())

router = APIRouter(prefix="/analytic")

def normalize_csv_param(val: Optional[str]) -> List[str]:
    """Normalize a comma-separated string into a list of stripped values."""
    if val:
        return [v.strip() for v in val.split(",") if v.strip()]
    return []

def normalize_date_param(val: Optional[str]) -> Optional[date]:
    """Convert a date string to date object or return None if invalid."""
    try:
        return datetime.strptime(val, "%Y-%m-%d").date() if val else None
    except ValueError:
        return None

@router.get("/recommended-cars", description="""
Returns a list of recommended cars with status 'RECOMMENDED' that match the provided filters.

📌 Filters can be passed as comma-separated strings for multi-value fields, e.g.:
- `make=Toyota,Ford`
- `vehicle_types=Sedan,SUV`
- `transmission=Automatic,Manual`

### Available Filters:
- **Mileage Range**: `mileage_start`, `mileage_end` (integers)
- **Owners Range**: `owners_start`, `owners_end` (integers)
- **Accident Count Range**: `accident_start`, `accident_end` (integers)
- **Year Range**: `year_start`, `year_end` (integers)
- **Vehicle Condition**: `vehicle_condition` (comma-separated strings)
- **Vehicle Types**: `vehicle_types` (comma-separated strings)
- **Make**: `make` (string)
- **Model**: `model` (string)
- **Predicted ROI Range**: `predicted_roi_start`, `predicted_roi_end` (floats)
- **Predicted Profit Margin Range**: `predicted_profit_margin_start`, `predicted_profit_margin_end` (floats)
- **Engine Type**: `engine_type` (comma-separated strings)
- **Transmission**: `transmission` (comma-separated strings)
- **Drive Train**: `drive_train` (comma-separated strings)
- **Cylinder**: `cylinder` (comma-separated strings)
- **Auction Names**: `auction_names` (comma-separated strings)
- **Body Style**: `body_style` (comma-separated strings)
""")
async def get_recommended_cars(
    db: AsyncSession = Depends(get_db),
    mileage_start: Optional[int] = Query(None, description="Minimum mileage"),
    mileage_end: Optional[int] = Query(None, description="Maximum mileage"),
    owners_start: Optional[int] = Query(None, description="Minimum number of owners"),
    owners_end: Optional[int] = Query(None, description="Maximum number of owners"),
    accident_start: Optional[int] = Query(None, description="Minimum accident count"),
    accident_end: Optional[int] = Query(None, description="Maximum accident count"),
    year_start: Optional[int] = Query(None, description="Minimum year"),
    year_end: Optional[int] = Query(None, description="Maximum year"),
    vehicle_condition: Optional[str] = Query(None, description="Comma-separated vehicle conditions"),
    vehicle_types: Optional[str] = Query(None, description="Comma-separated vehicle types"),
    make: Optional[str] = Query(None, description="Car make"),
    model: Optional[str] = Query(None, description="Car model"),
    predicted_roi_start: Optional[float] = Query(None, description="Minimum predicted ROI"),
    predicted_roi_end: Optional[float] = Query(None, description="Maximum predicted ROI"),
    predicted_profit_margin_start: Optional[float] = Query(None, description="Minimum predicted profit margin"),
    predicted_profit_margin_end: Optional[float] = Query(None, description="Maximum predicted profit margin"),
    engine_type: Optional[str] = Query(None, description="Comma-separated engine types"),
    transmission: Optional[str] = Query(None, description="Comma-separated transmissions"),
    drive_train: Optional[str] = Query(None, description="Comma-separated drive trains"),
    cylinder: Optional[str] = Query(None, description="Comma-separated cylinder counts"),
    auction_names: Optional[str] = Query(None, description="Comma-separated auction names"),
    body_style: Optional[str] = Query(None, description="Comma-separated body styles"),
):
    params = {
        "mileage_start": mileage_start,
        "mileage_end": mileage_end,
        "owners_start": owners_start,
        "owners_end": owners_end,
        "accident_start": accident_start,
        "accident_end": accident_end,
        "year_start": year_start,
        "year_end": year_end,
        "vehicle_condition": normalize_csv_param(vehicle_condition),
        "vehicle_types": normalize_csv_param(vehicle_types),
        "make": make,
        "model": model,
        "predicted_roi_start": predicted_roi_start,
        "predicted_roi_end": predicted_roi_end,
        "predicted_profit_margin_start": predicted_profit_margin_start,
        "predicted_profit_margin_end": predicted_profit_margin_end,
        "engine_type": normalize_csv_param(engine_type),
        "transmission": normalize_csv_param(transmission),
        "drive_train": normalize_csv_param(drive_train),
        "cylinder": normalize_csv_param(cylinder),
        "auction_names": normalize_csv_param(auction_names),
        "body_style": normalize_csv_param(body_style),
    }
    logger.debug("Query parameters for /recommended-cars: %s", params)

    query = text("""
        WITH us_states AS (
            SELECT unnest(ARRAY[
                'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA',
                'ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK',
                'OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY'
            ]) AS code
        ),
        locations_with_state AS (
            SELECT
                cars.*,
                CASE
                    WHEN location ~ E'\\([A-Z]{2}\\)' THEN REGEXP_REPLACE(location, E'.*\\(([A-Z]{2})\\).*', E'\\1')
                    WHEN location ~ E'^[A-Z]{2}\\s*-' THEN LEFT(location, 2)
                    ELSE NULL
                END AS state_code
            FROM cars
        )
        SELECT
            vehicle,
            vin,
            COALESCE(owners, 0) AS owners,
            COALESCE(accident_count, 0) AS accident,
            CONCAT(mileage, ' MI') AS odometer,
            CONCAT(engine, ' L') AS engine,
            CASE WHEN has_keys THEN 'Yes' ELSE 'No' END AS keys,
            auction AS source,
            lot,
            COALESCE(seller, '-') AS seller,
            location,
            COALESCE(date::text, '-') AS auction_date,
            current_bid
        FROM locations_with_state l
        JOIN us_states s ON l.state_code = s.code
        JOIN condition_assessments ca ON l.id = ca.car_id
        WHERE recommendation_status = 'RECOMMENDED'
          AND date >= CURRENT_DATE
          AND seller IS NOT NULL
          AND (COALESCE(:mileage_start, -1) IS NULL OR COALESCE(:mileage_end, 99999999) IS NULL OR mileage BETWEEN COALESCE(:mileage_start, 0) AND COALESCE(:mileage_end, 99999999))
          AND (COALESCE(:owners_start, -1) IS NULL OR COALESCE(:owners_end, 999) IS NULL OR owners BETWEEN COALESCE(:owners_start, 0) AND COALESCE(:owners_end, 999))
          AND (COALESCE(:accident_start, -1) IS NULL OR COALESCE(:accident_end, 999) IS NULL OR accident_count BETWEEN COALESCE(:accident_start, 0) AND COALESCE(:accident_end, 999))
          AND (COALESCE(:year_start, 1900) IS NULL OR COALESCE(:year_end, 2100) IS NULL OR year BETWEEN COALESCE(:year_start, 1900) AND COALESCE(:year_end, 2100))
          AND (array_length(:vehicle_condition::TEXT[], 1) = 0 OR ca.issue_description = ANY(:vehicle_condition::TEXT[]))
          AND (array_length(:vehicle_types::TEXT[], 1) = 0 OR vehicle_type = ANY(:vehicle_types::TEXT[]))
          AND (:make IS NULL OR make = :make)
          AND (:model IS NULL OR model = :model)
          AND (COALESCE(:predicted_roi_start, -100.0) IS NULL OR COALESCE(:predicted_roi_end, 1000.0) IS NULL OR predicted_roi BETWEEN COALESCE(:predicted_roi_start, -100.0) AND COALESCE(:predicted_roi_end, 1000.0))
          AND (COALESCE(:predicted_profit_margin_start, -100.0) IS NULL OR COALESCE(:predicted_profit_margin_end, 1000.0) IS NULL OR predicted_profit_margin BETWEEN COALESCE(:predicted_profit_margin_start, -100.0) AND COALESCE(:predicted_profit_margin_end, 1000.0))
          AND (array_length(:engine_type::TEXT[], 1) = 0 OR engine = ANY(:engine_type::TEXT[]))
          AND (array_length(:transmission::TEXT[], 1) = 0 OR transmission = ANY(:transmission::TEXT[]))
          AND (array_length(:drive_train::TEXT[], 1) = 0 OR drive_train = ANY(:drive_train::TEXT[]))
          AND (array_length(:cylinder::TEXT[], 1) = 0 OR engine_cylinder = ANY(:cylinder::TEXT[]))
          AND (array_length(:auction_names::TEXT[], 1) = 0 OR auction_name = ANY(:auction_names::TEXT[]))
          AND (array_length(:body_style::TEXT[], 1) = 0 OR body_style = ANY(:body_style::TEXT[]))
        LIMIT 50;
    """)

    result = await db.execute(query, params)
    return [dict(row) for row in result.fetchall()]

@router.get("/top-sellers", summary="Top 10 sellers by sold lots", description="""
Returns the top 10 sellers ranked by the number of sold lots, filtered by optional vehicle and sale criteria.

### Available Filters:
- **State Codes**: `state_codes` (comma-separated strings, e.g., 'CA,TX')
- **Cities**: `cities` (comma-separated strings)
- **Auctions**: `auctions` (comma-separated strings)
- **Mileage Range**: `mileage_start`, `mileage_end` (integers)
- **Owners Range**: `owners_start`, `owners_end` (integers)
- **Accident Count Range**: `accident_start`, `accident_end` (integers)
- **Year Range**: `year_start`, `year_end` (integers)
- **Vehicle Condition**: `vehicle_condition` (comma-separated strings)
- **Vehicle Types**: `vehicle_types` (comma-separated strings)
- **Make**: `make` (string)
- **Model**: `model` (string)
- **Predicted ROI Range**: `predicted_roi_start`, `predicted_roi_end` (floats)
- **Predicted Profit Margin Range**: `predicted_profit_margin_start`, `predicted_profit_margin_end` (floats)
- **Engine Type**: `engine_type` (comma-separated strings)
- **Transmission**: `transmission` (comma-separated strings)
- **Drive Train**: `drive_train` (comma-separated strings)
- **Cylinder**: `cylinder` (comma-separated strings)
- **Auction Names**: `auction_names` (comma-separated strings)
- **Body Style**: `body_style` (comma-separated strings)
- **Sale Date Range**: `sale_start`, `sale_end` (YYYY-MM-DD format)
""")
async def get_top_sellers(
    db: AsyncSession = Depends(get_db),
    state_codes: Optional[str] = Query(None, description="Comma-separated state codes, e.g., 'CA,TX'"),
    cities: Optional[str] = Query(None, description="Comma-separated city names"),
    auctions: Optional[str] = Query(None, description="Comma-separated auction names"),
    mileage_start: Optional[int] = Query(None, description="Minimum mileage"),
    mileage_end: Optional[int] = Query(None, description="Maximum mileage"),
    owners_start: Optional[int] = Query(None, description="Minimum number of owners"),
    owners_end: Optional[int] = Query(None, description="Maximum number of owners"),
    accident_start: Optional[int] = Query(None, description="Minimum accident count"),
    accident_end: Optional[int] = Query(None, description="Maximum accident count"),
    year_start: Optional[int] = Query(None, description="Minimum year"),
    year_end: Optional[int] = Query(None, description="Maximum year"),
    vehicle_condition: Optional[str] = Query(None, description="Comma-separated vehicle conditions"),
    vehicle_types: Optional[str] = Query(None, description="Comma-separated vehicle types"),
    make: Optional[str] = Query(None, description="Car make"),
    model: Optional[str] = Query(None, description="Car model"),
    predicted_roi_start: Optional[float] = Query(None, description="Minimum predicted ROI"),
    predicted_roi_end: Optional[float] = Query(None, description="Maximum predicted ROI"),
    predicted_profit_margin_start: Optional[float] = Query(None, description="Minimum predicted profit margin"),
    predicted_profit_margin_end: Optional[float] = Query(None, description="Maximum predicted profit margin"),
    engine_type: Optional[str] = Query(None, description="Comma-separated engine types"),
    transmission: Optional[str] = Query(None, description="Comma-separated transmissions"),
    drive_train: Optional[str] = Query(None, description="Comma-separated drive trains"),
    cylinder: Optional[str] = Query(None, description="Comma-separated cylinder counts"),
    auction_names: Optional[str] = Query(None, description="Comma-separated auction names"),
    body_style: Optional[str] = Query(None, description="Comma-separated body styles"),
    sale_start: Optional[str] = Query(None, description="Start date for sales (YYYY-MM-DD)"),
    sale_end: Optional[str] = Query(None, description="End date for sales (YYYY-MM-DD)"),
):
    params = {
        "state_codes": normalize_csv_param(state_codes),
        "cities": normalize_csv_param(cities),
        "auctions": normalize_csv_param(auctions),
        "vehicle_condition": normalize_csv_param(vehicle_condition),
        "vehicle_types": normalize_csv_param(vehicle_types),
        "engine_type": normalize_csv_param(engine_type),
        "transmission": normalize_csv_param(transmission),
        "drive_train": normalize_csv_param(drive_train),
        "cylinder": normalize_csv_param(cylinder),
        "auction_names": normalize_csv_param(auction_names),
        "body_style": normalize_csv_param(body_style),
        "mileage_start": mileage_start,
        "mileage_end": mileage_end,
        "owners_start": owners_start,
        "owners_end": owners_end,
        "accident_start": accident_start,
        "accident_end": accident_end,
        "year_start": year_start,
        "year_end": year_end,
        "make": make,
        "model": model,
        "predicted_roi_start": predicted_roi_start,
        "predicted_roi_end": predicted_roi_end,
        "predicted_profit_margin_start": predicted_profit_margin_start,
        "predicted_profit_margin_end": predicted_profit_margin_end,
        "sale_start": normalize_date_param(sale_start),
        "sale_end": normalize_date_param(sale_end),
    }
    logger.debug("Query parameters for /top-sellers: %s", params)

    query = text("""
        WITH us_states AS (
            SELECT unnest(ARRAY[
                'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA',
                'ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK',
                'OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY'
            ]) AS code
        ),
        locations_with_state AS (
            SELECT cars.*,
                CASE
                    WHEN location ~ E'\\([A-Z]{2}\\)' THEN REGEXP_REPLACE(location, E'.*\\(([A-Z]{2})\\).*', E'\\1')
                    WHEN location ~ E'^[A-Z]{2}\\s*-' THEN LEFT(location, 2)
                    ELSE NULL
                END AS state_code,
                CASE
                    WHEN location ~ E'\\([A-Z]{2}\\)' THEN TRIM(REGEXP_REPLACE(location, E'\\s*\\([A-Z]{2}\\)', ''))
                    WHEN location ~ E'^[A-Z]{2}\\s*-' THEN TRIM(SPLIT_PART(location, '-', 2))
                    ELSE NULL
                END AS city
            FROM cars
        )
        SELECT seller AS "Seller Name", COUNT(*) AS Lots
        FROM locations_with_state l
        JOIN us_states s ON l.state_code = s.code
        JOIN car_sale_history sh ON l.id = sh.car_id
        JOIN condition_assessments ca ON l.id = ca.car_id
        WHERE seller IS NOT NULL
          AND sh.status = 'Sold'
          AND sh.final_bid IS NOT NULL
          AND (array_length(:state_codes::TEXT[], 1) = 0 OR state_code = ANY(:state_codes::TEXT[]))
          AND (array_length(:cities::TEXT[], 1) = 0 OR city = ANY(:cities::TEXT[]))
          AND (array_length(:auctions::TEXT[], 1) = 0 OR auction = ANY(:auctions::TEXT[]))
          AND (COALESCE(:mileage_start, -1) IS NULL OR COALESCE(:mileage_end, 99999999) IS NULL OR mileage BETWEEN COALESCE(:mileage_start, 0) AND COALESCE(:mileage_end, 99999999))
          AND (COALESCE(:owners_start, -1) IS NULL OR COALESCE(:owners_end, 999) IS NULL OR owners BETWEEN COALESCE(:owners_start, 0) AND COALESCE(:owners_end, 999))
          AND (COALESCE(:accident_start, -1) IS NULL OR COALESCE(:accident_end, 999) IS NULL OR accident_count BETWEEN COALESCE(:accident_start, 0) AND COALESCE(:accident_end, 999))
          AND (COALESCE(:year_start, 1900) IS NULL OR COALESCE(:year_end, 2100) IS NULL OR year BETWEEN COALESCE(:year_start, 1900) AND COALESCE(:year_end, 2100))
          AND (array_length(:vehicle_condition::TEXT[], 1) = 0 OR ca.issue_description = ANY(:vehicle_condition::TEXT[]))
          AND (array_length(:vehicle_types::TEXT[], 1) = 0 OR vehicle_type = ANY(:vehicle_types::TEXT[]))
          AND (:make IS NULL OR make = :make)
          AND (:model IS NULL OR model = :model)
          AND (COALESCE(:predicted_roi_start, -100.0) IS NULL OR COALESCE(:predicted_roi_end, 1000.0) IS NULL OR predicted_roi BETWEEN COALESCE(:predicted_roi_start, -100.0) AND COALESCE(:predicted_roi_end, 1000.0))
          AND (COALESCE(:predicted_profit_margin_start, -100.0) IS NULL OR COALESCE(:predicted_profit_margin_end, 1000.0) IS NULL OR predicted_profit_margin BETWEEN COALESCE(:predicted_profit_margin_start, -100.0) AND COALESCE(:predicted_profit_margin_end, 1000.0))
          AND (array_length(:engine_type::TEXT[], 1) = 0 OR engine = ANY(:engine_type::TEXT[]))
          AND (array_length(:transmission::TEXT[], 1) = 0 OR transmission = ANY(:transmission::TEXT[]))
          AND (array_length(:drive_train::TEXT[], 1) = 0 OR drive_train = ANY(:drive_train::TEXT[]))
          AND (array_length(:cylinder::TEXT[], 1) = 0 OR engine_cylinder = ANY(:cylinder::TEXT[]))
          AND (array_length(:auction_names::TEXT[], 1) = 0 OR auction_name = ANY(:auction_names::TEXT[]))
          AND (array_length(:body_style::TEXT[], 1) = 0 OR body_style = ANY(:body_style::TEXT[]))
          AND (:sale_start IS NULL OR :sale_end IS NULL OR sh.date BETWEEN :sale_start AND :sale_end)
        GROUP BY seller
        ORDER BY Lots DESC
        LIMIT 10
    """)

    result = await db.execute(query, params)
    return [dict(row) for row in result.fetchall()]

@router.get("/analytics/sale-prices", summary="Average Sale Price Over Time", tags=["Analytics"], description="""
Returns the average final bid prices grouped by the specified time interval (day, week, or month) over a given period.

### Available Filters:
- **Interval Unit**: `interval_unit` (literal: 'day', 'week', 'month')
- **Interval Amount**: `interval_amount` (integer, number of intervals to look back)
- **Reference Date**: `reference_date` (date, default: today)
- **State Codes**: `state_codes` (comma-separated strings, e.g., 'CA,TX')
- **Cities**: `cities` (comma-separated strings)
- **Auctions**: `auctions` (comma-separated strings)
- **Mileage Range**: `mileage_start`, `mileage_end` (integers)
- **Owners Range**: `owners_start`, `owners_end` (integers)
- **Accident Count Range**: `accident_start`, `accident_end` (integers)
- **Year Range**: `year_start`, `year_end` (integers)
- **Vehicle Condition**: `vehicle_condition` (comma-separated strings)
- **Vehicle Types**: `vehicle_types` (comma-separated strings)
- **Make**: `make` (string)
- **Model**: `model` (string)
- **Predicted ROI Range**: `predicted_roi_start`, `predicted_roi_end` (floats)
- **Predicted Profit Margin Range**: `predicted_profit_margin_start`, `predicted_profit_margin_end` (floats)
- **Engine Type**: `engine_type` (comma-separated strings)
- **Transmission**: `transmission` (comma-separated strings)
- **Drive Train**: `drive_train` (comma-separated strings)
- **Cylinder**: `cylinder` (comma-separated strings)
- **Auction Names**: `auction_names` (comma-separated strings)
- **Body Style**: `body_style` (comma-separated strings)
- **Sale Date Range**: `sale_start`, `sale_end` (YYYY-MM-DD format)
""")
async def get_avg_sale_prices(
    interval_unit: Literal["day", "week", "month"] = Query("week", description="Time grouping unit (day, week, month)"),
    interval_amount: int = Query(12, description="Number of intervals to look back"),
    reference_date: Optional[date] = Query(None, description="End date of interval (default: today)"),
    state_codes: Optional[str] = Query(None, description="Comma-separated state codes, e.g., 'CA,TX'"),
    cities: Optional[str] = Query(None, description="Comma-separated city names"),
    auctions: Optional[str] = Query(None, description="Comma-separated auction names"),
    mileage_start: Optional[int] = Query(None, description="Minimum mileage"),
    mileage_end: Optional[int] = Query(None, description="Maximum mileage"),
    owners_start: Optional[int] = Query(None, description="Minimum number of owners"),
    owners_end: Optional[int] = Query(None, description="Maximum number of owners"),
    accident_start: Optional[int] = Query(None, description="Minimum accident count"),
    accident_end: Optional[int] = Query(None, description="Maximum accident count"),
    year_start: Optional[int] = Query(None, description="Minimum year"),
    year_end: Optional[int] = Query(None, description="Maximum year"),
    vehicle_condition: Optional[str] = Query(None, description="Comma-separated vehicle conditions"),
    vehicle_types: Optional[str] = Query(None, description="Comma-separated vehicle types"),
    make: Optional[str] = Query(None, description="Car make"),
    model: Optional[str] = Query(None, description="Car model"),
    predicted_roi_start: Optional[float] = Query(None, description="Minimum predicted ROI"),
    predicted_roi_end: Optional[float] = Query(None, description="Maximum predicted ROI"),
    predicted_profit_margin_start: Optional[float] = Query(None, description="Minimum predicted profit margin"),
    predicted_profit_margin_end: Optional[float] = Query(None, description="Maximum predicted profit margin"),
    engine_type: Optional[str] = Query(None, description="Comma-separated engine types"),
    transmission: Optional[str] = Query(None, description="Comma-separated transmissions"),
    drive_train: Optional[str] = Query(None, description="Comma-separated drive trains"),
    cylinder: Optional[str] = Query(None, description="Comma-separated cylinder counts"),
    auction_names: Optional[str] = Query(None, description="Comma-separated auction names"),
    body_style: Optional[str] = Query(None, description="Comma-separated body styles"),
    sale_start: Optional[date] = Query(None, description="Start date for sales (YYYY-MM-DD)"),
    sale_end: Optional[date] = Query(None, description="End date for sales (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    ref_date = reference_date or datetime.utcnow().date()
    logger.debug("Preparing query with params: interval_unit=%s, interval_amount=%s, ref_date=%s", interval_unit, interval_amount, ref_date)

    params = {
        "interval_unit": interval_unit,
        "interval_amount": interval_amount,
        "ref_date": ref_date,
        "state_codes": normalize_csv_param(state_codes),
        "cities": normalize_csv_param(cities),
        "auctions": normalize_csv_param(auctions),
        "mileage_start": mileage_start,
        "mileage_end": mileage_end,
        "owners_start": owners_start,
        "owners_end": owners_end,
        "accident_start": accident_start,
        "accident_end": accident_end,
        "year_start": year_start,
        "year_end": year_end,
        "vehicle_condition": normalize_csv_param(vehicle_condition),
        "vehicle_types": normalize_csv_param(vehicle_types),
        "make": make,
        "model": model,
        "predicted_roi_start": predicted_roi_start,
        "predicted_roi_end": predicted_roi_end,
        "predicted_profit_margin_start": predicted_profit_margin_start,
        "predicted_profit_margin_end": predicted_profit_margin_end,
        "engine_type": normalize_csv_param(engine_type),
        "transmission": normalize_csv_param(transmission),
        "drive_train": normalize_csv_param(drive_train),
        "cylinder": normalize_csv_param(cylinder),
        "auction_names": normalize_csv_param(auction_names),
        "body_style": normalize_csv_param(body_style),
        "sale_start": sale_start,
        "sale_end": sale_end,
    }
    logger.debug("Query parameters for /analytics/sale-prices: %s", params)

    query = text("""
    WITH us_states AS (
        SELECT unnest(ARRAY[
            'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA',
            'HI','ID','IL','IN','IA','KS','KY','LA','ME','MD',
            'MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
            'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC',
            'SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']) AS code
    ),
    locations_with_state AS (
        SELECT cars.*,
               CASE
                   WHEN location ~ E'\\([A-Z]{2}\\)' THEN REGEXP_REPLACE(location, E'.*\\(([A-Z]{2})\\).*', E'\\1')
                   WHEN location ~ E'^[A-Z]{2}\\s*-' THEN LEFT(location, 2)
                   ELSE NULL
               END AS state_code,
               CASE
                   WHEN location ~ E'\\([A-Z]{2}\\)' THEN TRIM(REGEXP_REPLACE(location, E'\\s*\\([A-Z]{2}\\)', ''))
                   WHEN location ~ E'^[A-Z]{2}\\s*-' THEN TRIM(SPLIT_PART(location, '-', 2))
                   ELSE NULL
               END AS city
        FROM cars
    )
    SELECT DATE_TRUNC(:interval_unit::text, sh.date) AS period,
           ROUND(AVG(sh.final_bid), 2) AS avg_price
    FROM locations_with_state l
    JOIN us_states s ON l.state_code = s.code
    JOIN car_sale_history sh ON l.id = sh.car_id
    JOIN condition_assessments ca ON l.id = ca.car_id
    WHERE sh.status = 'Sold'
      AND sh.final_bid IS NOT NULL
      AND sh.date >= :ref_date - (:interval_amount || ' ' || :interval_unit)::interval
      AND sh.date < :ref_date
      AND (array_length(:state_codes::TEXT[], 1) = 0 OR state_code = ANY(:state_codes::TEXT[]))
      AND (array_length(:cities::TEXT[], 1) = 0 OR city = ANY(:cities::TEXT[]))
      AND (array_length(:auctions::TEXT[], 1) = 0 OR auction = ANY(:auctions::TEXT[]))
      AND (COALESCE(:mileage_start, -1) IS NULL OR COALESCE(:mileage_end, 99999999) IS NULL OR mileage BETWEEN COALESCE(:mileage_start, 0) AND COALESCE(:mileage_end, 99999999))
      AND (COALESCE(:owners_start, -1) IS NULL OR COALESCE(:owners_end, 999) IS NULL OR owners BETWEEN COALESCE(:owners_start, 0) AND COALESCE(:owners_end, 999))
      AND (COALESCE(:accident_start, -1) IS NULL OR COALESCE(:accident_end, 999) IS NULL OR accident_count BETWEEN COALESCE(:accident_start, 0) AND COALESCE(:accident_end, 999))
      AND (COALESCE(:year_start, 1900) IS NULL OR COALESCE(:year_end, 2100) IS NULL OR year BETWEEN COALESCE(:year_start, 1900) AND COALESCE(:year_end, 2100))
      AND (array_length(:vehicle_condition::TEXT[], 1) = 0 OR ca.issue_description = ANY(:vehicle_condition::TEXT[]))
      AND (array_length(:vehicle_types::TEXT[], 1) = 0 OR vehicle_type = ANY(:vehicle_types::TEXT[]))
      AND (:make IS NULL OR make = :make)
      AND (:model IS NULL OR model = :model)
      AND (COALESCE(:predicted_roi_start, -100.0) IS NULL OR COALESCE(:predicted_roi_end, 1000.0) IS NULL OR predicted_roi BETWEEN COALESCE(:predicted_roi_start, -100.0) AND COALESCE(:predicted_roi_end, 1000.0))
      AND (COALESCE(:predicted_profit_margin_start, -100.0) IS NULL OR COALESCE(:predicted_profit_margin_end, 1000.0) IS NULL OR predicted_profit_margin BETWEEN COALESCE(:predicted_profit_margin_start, -100.0) AND COALESCE(:predicted_profit_margin_end, 1000.0))
      AND (array_length(:engine_type::TEXT[], 1) = 0 OR engine = ANY(:engine_type::TEXT[]))
      AND (array_length(:transmission::TEXT[], 1) = 0 OR transmission = ANY(:transmission::TEXT[]))
      AND (array_length(:drive_train::TEXT[], 1) = 0 OR drive_train = ANY(:drive_train::TEXT[]))
      AND (array_length(:cylinder::TEXT[], 1) = 0 OR engine_cylinder = ANY(:cylinder::TEXT[]))
      AND (array_length(:auction_names::TEXT[], 1) = 0 OR auction_name = ANY(:auction_names::TEXT[]))
      AND (array_length(:body_style::TEXT[], 1) = 0 OR body_style = ANY(:body_style::TEXT[]))
      AND (:sale_start IS NULL OR :sale_end IS NULL OR sh.date BETWEEN :sale_start AND :sale_end)
    GROUP BY period
    ORDER BY period;
    """)

    result = await db.execute(query, params)
    return [{"period": row[0].isoformat(), "avg_price": float(row[1])} for row in result.all()]