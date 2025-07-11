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
    if val:
        return [v.strip() for v in val.split(",") if v.strip()]
    return []

def normalize_date_param(val: Optional[str]) -> Optional[str]:
    try:
        return datetime.strptime(val, "%Y-%m-%d").date() if val else None
    except ValueError:
        return None


@router.get("/recommended-cars", description="""
Returns a list of recommended cars (with status 'RECOMMENDED') that match the provided filters.

📌 You can pass comma-separated string values for multi-value fields, like:
- `make=Toyota,Ford`
- `vehicle_types=Sedan,SUV`
- `transmission=Automatic,Manual`

Supported filters include:
- Mileage, owners, accident count, year
- Vehicle condition, type, engine, transmission, drivetrain, cylinders
- Make, model, ROI and profit margin ranges
- Auction, location, body style

Only cars with upcoming auction dates and assigned sellers will be returned.
""")
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
    vehicle_condition: Optional[str] = Query(None),
    vehicle_types: Optional[str] = Query(None),
    make: Optional[str] = None,
    model: Optional[str] = None,
    predicted_roi_start: Optional[float] = None,
    predicted_roi_end: Optional[float] = None,
    predicted_profit_margin_start: Optional[float] = None,
    predicted_profit_margin_end: Optional[float] = None,
    engine_type: Optional[str] = Query(None),
    transmission: Optional[str] = Query(None),
    drive_train: Optional[str] = Query(None),
    cylinder: Optional[str] = Query(None),
    auction_names: Optional[str] = Query(None),
    body_style: Optional[str] = Query(None),
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
    })

    return [dict(row) for row in result.fetchall()]


@router.get("/top-sellers", summary="Top 10 sellers by sold lots", description="Returns the top 10 sellers by lot count, filtered by optional vehicle and sale filters.")
async def get_top_sellers(
    db: AsyncSession = Depends(get_db),
    state_codes: Optional[str] = Query(None, description="Comma-separated e.g. 'CA,TX'"),
    cities: Optional[str] = Query(None),
    auctions: Optional[str] = Query(None),
    mileage_start: Optional[int] = None,
    mileage_end: Optional[int] = None,
    owners_start: Optional[int] = None,
    owners_end: Optional[int] = None,
    accident_start: Optional[int] = None,
    accident_end: Optional[int] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    vehicle_condition: Optional[str] = Query(None),
    vehicle_types: Optional[str] = Query(None),
    make: Optional[str] = None,
    model: Optional[str] = None,
    predicted_roi_start: Optional[float] = None,
    predicted_roi_end: Optional[float] = None,
    predicted_profit_margin_start: Optional[float] = None,
    predicted_profit_margin_end: Optional[float] = None,
    engine_type: Optional[str] = Query(None),
    transmission: Optional[str] = Query(None),
    drive_train: Optional[str] = Query(None),
    cylinder: Optional[str] = Query(None),
    auction_names: Optional[str] = Query(None),
    body_style: Optional[str] = Query(None),
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

    result = await db.execute(query, params)
    return [dict(row) for row in result.fetchall()]




@router.get("/analytics/sale-prices", summary="Average Sale Price Over Time", tags=["Analytics"])
async def get_avg_sale_prices(
    interval_unit: Literal["day", "week", "month"] = Query("week", description="Time grouping unit"),
    interval_amount: int = Query(12, description="Number of intervals to look back (e.g. 12 weeks)"),
    reference_date: Optional[date] = Query(None, description="End date of interval (default: today)"),
    state_codes: Optional[str] = Query(None, description="Comma-separated state codes"),
    cities: Optional[str] = Query(None, description="Comma-separated city names"),
    auctions: Optional[str] = Query(None, description="Comma-separated auction names"),
    mileage_start: Optional[int] = None,
    mileage_end: Optional[int] = None,
    owners_start: Optional[int] = None,
    owners_end: Optional[int] = None,
    accident_start: Optional[int] = None,
    accident_end: Optional[int] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    vehicle_condition: Optional[str] = Query(None, description="Comma-separated vehicle conditions"),
    vehicle_types: Optional[str] = Query(None, description="Comma-separated vehicle types"),
    make: Optional[str] = None,
    model: Optional[str] = None,
    predicted_roi_start: Optional[float] = None,
    predicted_roi_end: Optional[float] = None,
    predicted_profit_margin_start: Optional[float] = None,
    predicted_profit_margin_end: Optional[float] = None,
    engine_type: Optional[str] = Query(None, description="Comma-separated engine types"),
    transmission: Optional[str] = Query(None, description="Comma-separated transmissions"),
    drive_train: Optional[str] = Query(None, description="Comma-separated drive trains"),
    cylinder: Optional[str] = Query(None, description="Comma-separated cylinder counts"),
    auction_names: Optional[str] = Query(None, description="Comma-separated auction names"),
    body_style: Optional[str] = Query(None, description="Comma-separated body styles"),
    sale_start: Optional[date] = None,
    sale_end: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns average final bid prices grouped by day, week, or month for a given time interval.
    Useful for tracking pricing trends with flexible filtering by location, specs, and sale info.
    """

    ref_date = reference_date or datetime.utcnow().date()

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

    result = await db.execute(query, params)
    return [{"period": row[0], "avg_price": float(row[1])} for row in result.all()]