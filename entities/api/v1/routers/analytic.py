import logging
import sys
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db

# Configure logging with enhanced debugging (коментуємо логування в файл)
logger = logging.getLogger("admin_router")
logger.setLevel(logging.DEBUG)

# Ensure logs are visible in console
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - [RequestID: %(request_id)s] - [UserID: %(user_id)s] - %(message)s"
)
console_handler = logging.StreamHandler(stream=sys.stdout)  # Explicitly use stdout
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.DEBUG)  # Set to DEBUG for all messages
logger.addHandler(console_handler)

# class ContextFilter(logging.Filter):
#     def filter(self, record):
#         record.request_id = getattr(record, "request_id", "N/A")
#         record.user_id = getattr(record, "user_id", "N/A")
#         return True
#
# logger.addFilter(ContextFilter())

router = APIRouter(prefix="/analytic")


def normalize_csv_param(val: Optional[str]) -> list[str]:
    """Normalize a comma-separated string into a list of stripped values."""
    # logger.debug("Normalizing CSV param: %s", val)
    if val:
        result = [v.strip() for v in val.split(",") if v.strip()]
        # logger.debug("Normalized result: %s", result)
        return result
    return []


def normalize_date_param(val: Optional[str]) -> Optional[datetime]:
    """Convert a date string to datetime object or return None if invalid."""
    # logger.debug("Normalizing date param: %s", val)
    try:
        result = datetime.strptime(val, "%Y-%m-%d") if val else None
        # logger.debug("Normalized date result: %s", result)
        return result
    except ValueError as e:
        # logger.error("Date normalization failed: %s", str(e))
        return None


@router.get(
    "/recommended-cars",
    description="""
Returns a list of recommended cars with status 'RECOMMENDED' that match the provided filters.
...
""",
)
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
    # logger.debug("Received request for /recommended-cars with params: mileage_start=%s, mileage_end=%s, ...", mileage_start, mileage_end)
    params = [
        mileage_start,
        mileage_end,
        owners_start,
        owners_end,
        accident_start,
        accident_end,
        year_start,
        year_end,
        make,
        model,
        predicted_roi_start,
        predicted_roi_end,
        predicted_profit_margin_start,
        predicted_profit_margin_end,
        normalize_csv_param(vehicle_condition),
        normalize_csv_param(vehicle_types),
        normalize_csv_param(engine_type),
        normalize_csv_param(transmission),
        normalize_csv_param(drive_train),
        normalize_csv_param(cylinder),
        normalize_csv_param(auction_names),
        normalize_csv_param(body_style),
    ]
    # logger.debug("Prepared params for query: %s", params)

    query = text(
        """
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
          AND (COALESCE($1, -1) IS NULL OR COALESCE($2, 99999999) IS NULL OR mileage BETWEEN COALESCE($1, 0) AND COALESCE($2, 99999999))
          AND (COALESCE($3, -1) IS NULL OR COALESCE($4, 999) IS NULL OR owners BETWEEN COALESCE($3, 0) AND COALESCE($4, 999))
          AND (COALESCE($5, -1) IS NULL OR COALESCE($6, 999) IS NULL OR accident_count BETWEEN COALESCE($5, 0) AND COALESCE($6, 999))
          AND (COALESCE($7, 1900) IS NULL OR COALESCE($8, 2100) IS NULL OR year BETWEEN COALESCE($7, 1900) AND COALESCE($8, 2100))
          AND (array_length($15::TEXT[], 1) = 0 OR ca.issue_description = ANY($15::TEXT[]))
          AND (array_length($16::TEXT[], 1) = 0 OR vehicle_type = ANY($16::TEXT[]))
          AND ($9 IS NULL OR make = $9)
          AND ($10 IS NULL OR model = $10)
          AND (COALESCE($11, -100.0) IS NULL OR COALESCE($12, 1000.0) IS NULL OR predicted_roi BETWEEN COALESCE($11, -100.0) AND COALESCE($12, 1000.0))
          AND (COALESCE($13, -100.0) IS NULL OR COALESCE($14, 1000.0) IS NULL OR predicted_profit_margin BETWEEN COALESCE($13, -100.0) AND COALESCE($14, 1000.0))
          AND (array_length($17::TEXT[], 1) = 0 OR engine = ANY($17::TEXT[]))
          AND (array_length($18::TEXT[], 1) = 0 OR transmission = ANY($18::TEXT[]))
          AND (array_length($19::TEXT[], 1) = 0 OR drive_train = ANY($19::TEXT[]))
          AND (array_length($20::TEXT[], 1) = 0 OR engine_cylinder = ANY($20::TEXT[]))
          AND (array_length($21::TEXT[], 1) = 0 OR auction_name = ANY($21::TEXT[]))
          AND (array_length($22::TEXT[], 1) = 0 OR body_style = ANY($22::TEXT[]))
        LIMIT 50;
    """
    )

    # logger.debug("Executing query for /recommended-cars with params: %s", params)
    result = await db.execute(query, params)
    # logger.debug("Query executed successfully")
    return [dict(row) for row in result.fetchall()]


@router.get(
    "/top-sellers",
    summary="Top 10 sellers by sold lots",
    description="""
Returns the top 10 sellers ranked by the number of sold lots, filtered by optional vehicle and sale criteria.
...
""",
)
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
    # logger.debug("Received request for /top-sellers with params: state_codes=%s, mileage_start=%s, ...", state_codes, mileage_start)
    params = [
        normalize_csv_param(state_codes),
        normalize_csv_param(cities),
        normalize_csv_param(auctions),
        mileage_start,
        mileage_end,
        owners_start,
        owners_end,
        accident_start,
        accident_end,
        year_start,
        year_end,
        make,
        model,
        predicted_roi_start,
        predicted_roi_end,
        predicted_profit_margin_start,
        predicted_profit_margin_end,
        normalize_csv_param(vehicle_condition),
        normalize_csv_param(vehicle_types),
        normalize_csv_param(engine_type),
        normalize_csv_param(transmission),
        normalize_csv_param(drive_train),
        normalize_csv_param(cylinder),
        normalize_csv_param(auction_names),
        normalize_csv_param(body_style),
        normalize_date_param(sale_start),
        normalize_date_param(sale_end),
    ]
    # logger.debug("Prepared params for query: %s", params)

    query = text(
        """
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
          AND (array_length($1::TEXT[], 1) = 0 OR state_code = ANY($1::TEXT[]))
          AND (array_length($2::TEXT[], 1) = 0 OR city = ANY($2::TEXT[]))
          AND (array_length($3::TEXT[], 1) = 0 OR auction = ANY($3::TEXT[]))
          AND (COALESCE($4, -1) IS NULL OR COALESCE($5, 99999999) IS NULL OR mileage BETWEEN COALESCE($4, 0) AND COALESCE($5, 99999999))
          AND (COALESCE($6, -1) IS NULL OR COALESCE($7, 999) IS NULL OR owners BETWEEN COALESCE($6, 0) AND COALESCE($7, 999))
          AND (COALESCE($8, -1) IS NULL OR COALESCE($9, 999) IS NULL OR accident_count BETWEEN COALESCE($8, 0) AND COALESCE($9, 999))
          AND (COALESCE($10, 1900) IS NULL OR COALESCE($11, 2100) IS NULL OR year BETWEEN COALESCE($10, 1900) AND COALESCE($11, 2100))
          AND (array_length($18::TEXT[], 1) = 0 OR ca.issue_description = ANY($18::TEXT[]))
          AND (array_length($19::TEXT[], 1) = 0 OR vehicle_type = ANY($19::TEXT[]))
          AND ($12 IS NULL OR make = $12)
          AND ($13 IS NULL OR model = $13)
          AND (COALESCE($14, -100.0) IS NULL OR COALESCE($15, 1000.0) IS NULL OR predicted_roi BETWEEN COALESCE($14, -100.0) AND COALESCE($15, 1000.0))
          AND (COALESCE($16, -100.0) IS NULL OR COALESCE($17, 1000.0) IS NULL OR predicted_profit_margin BETWEEN COALESCE($16, -100.0) AND COALESCE($17, 1000.0))
          AND (array_length($20::TEXT[], 1) = 0 OR engine = ANY($20::TEXT[]))
          AND (array_length($21::TEXT[], 1) = 0 OR transmission = ANY($21::TEXT[]))
          AND (array_length($22::TEXT[], 1) = 0 OR drive_train = ANY($22::TEXT[]))
          AND (array_length($23::TEXT[], 1) = 0 OR engine_cylinder = ANY($23::TEXT[]))
          AND (array_length($24::TEXT[], 1) = 0 OR auction_name = ANY($24::TEXT[]))
          AND (array_length($25::TEXT[], 1) = 0 OR body_style = ANY($25::TEXT[]))
          AND ($26 IS NULL OR $27 IS NULL OR sh.date BETWEEN $26 AND $27)
        GROUP BY seller
        ORDER BY Lots DESC
        LIMIT 10
    """
    )

    # logger.debug("Executing query for /top-sellers with params: %s", params)
    result = await db.execute(query, params)
    # logger.debug("Query executed successfully")
    return [dict(row) for row in result.fetchall()]


@router.get(
    "/analytics/sale-prices",
    summary="Average Sale Price Over Time",
    tags=["Analytics"],
    description="""
Returns the average final bid prices grouped by the specified time interval (day, week, or month) over a given period.
...
""",
)
async def get_avg_sale_prices(
    interval_unit: Literal["day", "week", "month"] = Query(
        "week", description="Time grouping unit (day, week, month)"
    ),
    interval_amount: int = Query(12, description="Number of intervals to look back"),
    reference_date: Optional[datetime] = Query(None, description="End date of interval (default: today)"),
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
    sale_start: Optional[datetime] = Query(None, description="Start date for sales (YYYY-MM-DD)"),
    sale_end: Optional[datetime] = Query(None, description="End date for sales (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    # logger.debug("Received request for /analytics/sale-prices with params: interval_unit=%s, interval_amount=%s, ...", interval_unit, interval_amount)
    ref_date = reference_date or datetime.utcnow()
    params = [
        interval_unit,
        interval_amount,
        ref_date,
        normalize_csv_param(state_codes),
        normalize_csv_param(cities),
        normalize_csv_param(auctions),
        mileage_start,
        mileage_end,
        owners_start,
        owners_end,
        accident_start,
        accident_end,
        year_start,
        year_end,
        normalize_csv_param(vehicle_condition),
        normalize_csv_param(vehicle_types),
        make,
        model,
        predicted_roi_start,
        predicted_roi_end,
        predicted_profit_margin_start,
        predicted_profit_margin_end,
        normalize_csv_param(engine_type),
        normalize_csv_param(transmission),
        normalize_csv_param(drive_train),
        normalize_csv_param(cylinder),
        normalize_csv_param(auction_names),
        normalize_csv_param(body_style),
        sale_start,
        sale_end,
    ]
    # logger.debug("Prepared params for query: %s", params)

    query = text(
        """
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
        SELECT DATE_TRUNC($1::text, sh.date) AS period,
               ROUND(AVG(sh.final_bid), 2) AS avg_price
        FROM locations_with_state l
        JOIN us_states s ON l.state_code = s.code
        JOIN car_sale_history sh ON l.id = sh.car_id
        JOIN condition_assessments ca ON l.id = ca.car_id
        WHERE sh.status = 'Sold'
          AND sh.final_bid IS NOT NULL
          AND sh.date >= $3 - ($2 || ' ' || $1)::interval
          AND sh.date < $3
          AND (array_length($4::TEXT[], 1) = 0 OR state_code = ANY($4::TEXT[]))
          AND (array_length($5::TEXT[], 1) = 0 OR city = ANY($5::TEXT[]))
          AND (array_length($6::TEXT[], 1) = 0 OR auction = ANY($6::TEXT[]))
          AND (COALESCE($7, -1) IS NULL OR COALESCE($8, 99999999) IS NULL OR mileage BETWEEN COALESCE($7, 0) AND COALESCE($8, 99999999))
          AND (COALESCE($9, -1) IS NULL OR COALESCE($10, 999) IS NULL OR owners BETWEEN COALESCE($9, 0) AND COALESCE($10, 999))
          AND (COALESCE($11, -1) IS NULL OR COALESCE($12, 999) IS NULL OR accident_count BETWEEN COALESCE($11, 0) AND COALESCE($12, 999))
          AND (COALESCE($13, 1900) IS NULL OR COALESCE($14, 2100) IS NULL OR year BETWEEN COALESCE($13, 1900) AND COALESCE($14, 2100))
          AND (array_length($15::TEXT[], 1) = 0 OR ca.issue_description = ANY($15::TEXT[]))
          AND (array_length($16::TEXT[], 1) = 0 OR vehicle_type = ANY($16::TEXT[]))
          AND ($17 IS NULL OR make = $17)
          AND ($18 IS NULL OR model = $18)
          AND (COALESCE($19, -100.0) IS NULL OR COALESCE($20, 1000.0) IS NULL OR predicted_roi BETWEEN COALESCE($19, -100.0) AND COALESCE($20, 1000.0))
          AND (COALESCE($21, -100.0) IS NULL OR COALESCE($22, 1000.0) IS NULL OR predicted_profit_margin BETWEEN COALESCE($21, -100.0) AND COALESCE($22, 1000.0))
          AND (array_length($23::TEXT[], 1) = 0 OR engine = ANY($23::TEXT[]))
          AND (array_length($24::TEXT[], 1) = 0 OR transmission = ANY($24::TEXT[]))
          AND (array_length($25::TEXT[], 1) = 0 OR drive_train = ANY($25::TEXT[]))
          AND (array_length($26::TEXT[], 1) = 0 OR engine_cylinder = ANY($26::TEXT[]))
          AND (array_length($27::TEXT[], 1) = 0 OR auction_name = ANY($27::TEXT[]))
          AND (array_length($28::TEXT[], 1) = 0 OR body_style = ANY($28::TEXT[]))
          AND ($29 IS NULL OR $30 IS NULL OR sh.date BETWEEN $29 AND $30)
        GROUP BY period
        ORDER BY period;
    """
    )

    # logger.debug("Executing query for /analytics/sale-prices with params: %s", params)
    result = await db.execute(query, params)
    # logger.debug("Query executed successfully")
    return [{"period": row[0].isoformat(), "avg_price": float(row[1])} for row in result.all()]
