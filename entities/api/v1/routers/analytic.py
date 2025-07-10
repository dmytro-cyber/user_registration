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


@router.get("/recommended-cars")
async def get_recommended_cars(
    db: AsyncSession = Depends(get_db),
    mileage_start: Optional[int] = None,
    mileage_end: Optional[int] = None,
    owners_start: Optional[int] = None,
    owners_end: Optional[int] = None,
    accident_start: Optional[int] = None,
    accident_end: Optional[int] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    vehicle_condition: Optional[List[str]] = Query(None),
    vehicle_types: Optional[List[str]] = Query(None),
    make: Optional[str] = None,
    model: Optional[str] = None,
    predicted_roi_start: Optional[float] = None,
    predicted_roi_end: Optional[float] = None,
    predicted_profit_margin_start: Optional[float] = None,
    predicted_profit_margin_end: Optional[float] = None,
    engine_type: Optional[List[str]] = Query(None),
    transmission: Optional[List[str]] = Query(None),
    drive_train: Optional[List[str]] = Query(None),
    cylinder: Optional[List[str]] = Query(None),
    auction_names: Optional[List[str]] = Query(None),
    body_style: Optional[List[str]] = Query(None),
):
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
          AND (:mileage_start IS NULL OR :mileage_end IS NULL OR mileage BETWEEN :mileage_start AND :mileage_end)
          AND (:owners_start IS NULL OR :owners_end IS NULL OR owners BETWEEN :owners_start AND :owners_end)
          AND (:accident_start IS NULL OR :accident_end IS NULL OR accident_count BETWEEN :accident_start AND :accident_end)
          AND (:year_start IS NULL OR :year_end IS NULL OR year BETWEEN :year_start AND :year_end)
          AND (:vehicle_condition IS NULL OR ca.issue_description = ANY(:vehicle_condition))
          AND (:vehicle_types IS NULL OR vehicle_type = ANY(:vehicle_types))
          AND (:make IS NULL OR make = :make)
          AND (:model IS NULL OR model = :model)
          AND (:predicted_roi_start IS NULL OR :predicted_roi_end IS NULL OR predicted_roi BETWEEN :predicted_roi_start AND :predicted_roi_end)
          AND (:predicted_profit_margin_start IS NULL OR :predicted_profit_margin_end IS NULL OR predicted_profit_margin BETWEEN :predicted_profit_margin_start AND :predicted_profit_margin_end)
          AND (:engine_type IS NULL OR engine = ANY(:engine_type))
          AND (:transmission IS NULL OR transmision = ANY(:transmission))
          AND (:drive_train IS NULL OR drive_type = ANY(:drive_train))
          AND (:cylinder IS NULL OR engine_cylinder = ANY(:cylinder))
          AND (:auction_names IS NULL OR auction_name = ANY(:auction_names))
          AND (:body_style IS NULL OR body_style = ANY(:body_style))
        LIMIT 50;
    """)

    result = await db.execute(query, {
        "mileage_start": mileage_start,
        "mileage_end": mileage_end,
        "owners_start": owners_start,
        "owners_end": owners_end,
        "accident_start": accident_start,
        "accident_end": accident_end,
        "year_start": year_start,
        "year_end": year_end,
        "vehicle_condition": vehicle_condition,
        "vehicle_types": vehicle_types,
        "make": make,
        "model": model,
        "predicted_roi_start": predicted_roi_start,
        "predicted_roi_end": predicted_roi_end,
        "predicted_profit_margin_start": predicted_profit_margin_start,
        "predicted_profit_margin_end": predicted_profit_margin_end,
        "engine_type": engine_type,
        "transmission": transmission,
        "drive_train": drive_train,
        "cylinder": cylinder,
        "auction_names": auction_names,
        "body_style": body_style,
    })

    return [dict(row) for row in result.fetchall()]


@router.get("/top-sellers")
async def get_top_sellers(
    db: AsyncSession = Depends(get_db),
    state_codes: Optional[List[str]] = Query(None),
    cities: Optional[List[str]] = Query(None),
    auctions: Optional[List[str]] = Query(None),
    mileage_start: Optional[int] = None,
    mileage_end: Optional[int] = None,
    owners_start: Optional[int] = None,
    owners_end: Optional[int] = None,
    accident_start: Optional[int] = None,
    accident_end: Optional[int] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    vehicle_condition: Optional[List[str]] = Query(None),
    vehicle_types: Optional[List[str]] = Query(None),
    make: Optional[str] = None,
    model: Optional[str] = None,
    predicted_roi_start: Optional[float] = None,
    predicted_roi_end: Optional[float] = None,
    predicted_profit_margin_start: Optional[float] = None,
    predicted_profit_margin_end: Optional[float] = None,
    engine_type: Optional[List[str]] = Query(None),
    transmission: Optional[List[str]] = Query(None),
    drive_train: Optional[List[str]] = Query(None),
    cylinder: Optional[List[str]] = Query(None),
    auction_names: Optional[List[str]] = Query(None),
    body_style: Optional[List[str]] = Query(None),
    sale_start: Optional[str] = None,
    sale_end: Optional[str] = None,
):
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
          AND (:state_codes IS NULL OR state_code = ANY(:state_codes))
          AND (:cities IS NULL OR city = ANY(:cities))
          AND (:auctions IS NULL OR auction = ANY(:auctions))
          AND (:mileage_start IS NULL OR :mileage_end IS NULL OR mileage BETWEEN :mileage_start AND :mileage_end)
          AND (:owners_start IS NULL OR :owners_end IS NULL OR owners BETWEEN :owners_start AND :owners_end)
          AND (:accident_start IS NULL OR :accident_end IS NULL OR accident_count BETWEEN :accident_start AND :accident_end)
          AND (:year_start IS NULL OR :year_end IS NULL OR year BETWEEN :year_start AND :year_end)
          AND (:vehicle_condition IS NULL OR ca.issue_description = ANY(:vehicle_condition))
          AND (:vehicle_types IS NULL OR vehicle_type = ANY(:vehicle_types))
          AND (:make IS NULL OR make = :make)
          AND (:model IS NULL OR model = :model)
          AND (:predicted_roi_start IS NULL OR :predicted_roi_end IS NULL OR predicted_roi BETWEEN :predicted_roi_start AND :predicted_roi_end)
          AND (:predicted_profit_margin_start IS NULL OR :predicted_profit_margin_end IS NULL OR predicted_profit_margin BETWEEN :predicted_profit_margin_start AND :predicted_profit_margin_end)
          AND (:engine_type IS NULL OR engine = ANY(:engine_type))
          AND (:transmission IS NULL OR transmision = ANY(:transmission))
          AND (:drive_train IS NULL OR drive_type = ANY(:drive_train))
          AND (:cylinder IS NULL OR engine_cylinder = ANY(:cylinder))
          AND (:auction_names IS NULL OR auction_name = ANY(:auction_names))
          AND (:body_style IS NULL OR body_style = ANY(:body_style))
          AND (:sale_start IS NULL OR :sale_end IS NULL OR sh.date BETWEEN :sale_start AND :sale_end)
        GROUP BY seller
        ORDER BY Lots DESC
        LIMIT 10
    """)

    params = {
        "state_codes": state_codes,
        "cities": cities,
        "auctions": auctions,
        "mileage_start": mileage_start,
        "mileage_end": mileage_end,
        "owners_start": owners_start,
        "owners_end": owners_end,
        "accident_start": accident_start,
        "accident_end": accident_end,
        "year_start": year_start,
        "year_end": year_end,
        "vehicle_condition": vehicle_condition,
        "vehicle_types": vehicle_types,
        "make": make,
        "model": model,
        "predicted_roi_start": predicted_roi_start,
        "predicted_roi_end": predicted_roi_end,
        "predicted_profit_margin_start": predicted_profit_margin_start,
        "predicted_profit_margin_end": predicted_profit_margin_end,
        "engine_type": engine_type,
        "transmission": transmission,
        "drive_train": drive_train,
        "cylinder": cylinder,
        "auction_names": auction_names,
        "body_style": body_style,
        "sale_start": sale_start,
        "sale_end": sale_end,
    }

    result = await db.execute(query, params)
    return [dict(row) for row in result.fetchall()]


@router.get("/analytics/sale-prices", summary="Average Sale Price Over Time", tags=["Analytics"])
async def get_avg_sale_prices(
    interval_unit: Literal["day", "week", "month"] = Query("week", description="Time grouping unit: 'day', 'week', or 'month'"),
    interval_amount: int = Query(12, description="How many units back to look (e.g. 12 weeks or months)"),
    reference_date: Optional[date] = Query(None, description="End date for interval (default: today)"),
    state_codes: Optional[List[str]] = Query(None, description="List of US state codes (e.g., ['CA', 'NY'])"),
    cities: Optional[List[str]] = Query(None, description="List of city names"),
    auctions: Optional[List[str]] = Query(None, description="Auction names to filter"),
    mileage_start: Optional[int] = Query(None, description="Minimum mileage"),
    mileage_end: Optional[int] = Query(None, description="Maximum mileage"),
    owners_start: Optional[int] = Query(None, description="Minimum number of owners"),
    owners_end: Optional[int] = Query(None, description="Maximum number of owners"),
    accident_start: Optional[int] = Query(None, description="Minimum number of accidents"),
    accident_end: Optional[int] = Query(None, description="Maximum number of accidents"),
    year_start: Optional[int] = Query(None, description="Minimum vehicle year"),
    year_end: Optional[int] = Query(None, description="Maximum vehicle year"),
    vehicle_condition: Optional[List[str]] = Query(None, description="List of vehicle issue descriptions"),
    vehicle_types: Optional[List[str]] = Query(None, description="Vehicle types (e.g., ['SUV', 'Sedan'])"),
    make: Optional[str] = Query(None, description="Vehicle make"),
    model: Optional[str] = Query(None, description="Vehicle model"),
    predicted_roi_start: Optional[float] = Query(None, description="Min predicted ROI"),
    predicted_roi_end: Optional[float] = Query(None, description="Max predicted ROI"),
    predicted_profit_margin_start: Optional[float] = Query(None, description="Min profit margin"),
    predicted_profit_margin_end: Optional[float] = Query(None, description="Max profit margin"),
    engine_type: Optional[List[str]] = Query(None, description="List of engine types"),
    transmission: Optional[List[str]] = Query(None, description="List of transmissions"),
    drive_train: Optional[List[str]] = Query(None, description="List of drive types"),
    cylinder: Optional[List[int]] = Query(None, description="List of engine cylinder counts"),
    auction_names: Optional[List[str]] = Query(None, description="List of auction names"),
    body_style: Optional[List[str]] = Query(None, description="List of body styles"),
    sale_start: Optional[date] = Query(None, description="Start date of sale range"),
    sale_end: Optional[date] = Query(None, description="End date of sale range"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns average final bid prices grouped by a time interval (day/week/month) over a dynamic historical window.

    Useful for visualizing pricing trends across time, filtered by location, vehicle type, seller attributes, and sale history.
    """

    ref_date = reference_date or datetime.utcnow().date()

    query = """
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
      AND (:state_codes IS NULL OR state_code = ANY(:state_codes))
      AND (:cities IS NULL OR city = ANY(:cities))
      AND (:auctions IS NULL OR auction = ANY(:auctions))
      AND (:mileage_start IS NULL OR :mileage_end IS NULL OR mileage BETWEEN :mileage_start AND :mileage_end)
      AND (:owners_start IS NULL OR :owners_end IS NULL OR owners BETWEEN :owners_start AND :owners_end)
      AND (:accident_start IS NULL OR :accident_end IS NULL OR accident_count BETWEEN :accident_start AND :accident_end)
      AND (:year_start IS NULL OR :year_end IS NULL OR year BETWEEN :year_start AND :year_end)
      AND (:vehicle_condition IS NULL OR ca.issue_description = ANY(:vehicle_condition))
      AND (:vehicle_types IS NULL OR vehicle_type = ANY(:vehicle_types))
      AND (:make IS NULL OR make = :make)
      AND (:model IS NULL OR model = :model)
      AND (:predicted_roi_start IS NULL OR :predicted_roi_end IS NULL OR predicted_roi BETWEEN :predicted_roi_start AND :predicted_roi_end)
      AND (:predicted_profit_margin_start IS NULL OR :predicted_profit_margin_end IS NULL OR predicted_profit_margin BETWEEN :predicted_profit_margin_start AND :predicted_profit_margin_end)
      AND (:engine_type IS NULL OR engine = ANY(:engine_type))
      AND (:transmission IS NULL OR transmision = ANY(:transmission))
      AND (:drive_train IS NULL OR drive_type = ANY(:drive_train))
      AND (:cylinder IS NULL OR engine_cylinder = ANY(:cylinder))
      AND (:auction_names IS NULL OR auction_name = ANY(:auction_names))
      AND (:body_style IS NULL OR body_style = ANY(:body_style))
      AND (:sale_start IS NULL OR :sale_end IS NULL OR sh.date BETWEEN :sale_start AND :sale_end)
    GROUP BY period
    ORDER BY period;
    """

    params = locals()
    result = await db.execute(query, params)
    return [{"period": row[0], "avg_price": float(row[1])} for row in result.all()]