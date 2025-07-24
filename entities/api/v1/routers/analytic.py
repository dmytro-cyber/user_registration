import logging
import sys
from datetime import datetime
from typing import Literal, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db
from models import CarModel, CarSaleHistoryModel, ConditionAssessmentModel

# Configure logging with enhanced debugging
logger = logging.getLogger("admin_router")
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - [RequestID: %(request_id)s] - [UserID: %(user_id)s] - %(message)s"
)
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

router = APIRouter(prefix="/analytic")


def normalize_csv_param(val: Optional[str]) -> list[str]:
    """Normalize a comma-separated string into a list of stripped values."""
    if val:
        return [v.strip() for v in val.split(",") if v.strip()]
    return []


def normalize_date_param(val: Optional[str]) -> Optional[datetime]:
    """Convert a date string to datetime object or return None if invalid."""
    try:
        return datetime.strptime(val, "%Y-%m-%d") if val else None
    except ValueError:
        return None


@router.get("/recommended-cars")
async def get_filtered_cars(
    mileage_start: Optional[int] = Query(None),
    mileage_end: Optional[int] = Query(None),
    owners_start: Optional[int] = Query(None),
    owners_end: Optional[int] = Query(None),
    accident_start: Optional[int] = Query(None),
    accident_end: Optional[int] = Query(None),
    year_start: Optional[int] = Query(None),
    year_end: Optional[int] = Query(None),
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    engine: Optional[str] = Query(None),
    transmision: Optional[str] = Query(None),
    drive_type: Optional[str] = Query(None),
    engine_cylinder: Optional[str] = Query(None),
    vehicle_type: Optional[str] = Query(None),
    body_style: Optional[str] = Query(None),
    auction_name: Optional[str] = Query(None),
    recommendation_status: Optional[str] = Query(None),
    predicted_roi_min: Optional[float] = Query(None),
    predicted_roi_max: Optional[float] = Query(None),
    predicted_profit_margin_min: Optional[float] = Query(None),
    predicted_profit_margin_max: Optional[float] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    # Нормалізація параметрів
    make_list = normalize_csv_param(make)
    model_list = normalize_csv_param(model)
    engine_list = normalize_csv_param(engine)
    transmision_list = normalize_csv_param(transmision)
    drive_type_list = normalize_csv_param(drive_type)
    engine_cylinder_list = normalize_csv_param(engine_cylinder)
    vehicle_type_list = normalize_csv_param(vehicle_type)
    body_style_list = normalize_csv_param(body_style)
    auction_name_list = normalize_csv_param(auction_name)
    recommendation_status_list = normalize_csv_param(recommendation_status)

    filters = []

    if mileage_start is not None:
        filters.append(CarModel.mileage >= mileage_start)
    if mileage_end is not None:
        filters.append(CarModel.mileage <= mileage_end)

    if owners_start is not None:
        filters.append(CarModel.owners >= owners_start)
    if owners_end is not None:
        filters.append(CarModel.owners <= owners_end)

    if accident_start is not None:
        filters.append(CarModel.accident_count >= accident_start)
    if accident_end is not None:
        filters.append(CarModel.accident_count <= accident_end)

    if year_start is not None:
        filters.append(CarModel.year >= year_start)
    if year_end is not None:
        filters.append(CarModel.year <= year_end)

    if make_list:
        filters.append(CarModel.make.ilike(f"%{make_list[0]}%"))  # Беремо перший елемент, якщо список
    if model_list:
        filters.append(CarModel.model.ilike(f"%{model_list[0]}%"))
    if engine_list:
        filters.append(CarModel.engine.ilike(f"%{engine_list[0]}%"))
    if transmision_list:
        filters.append(CarModel.transmision.ilike(f"%{transmision_list[0]}%"))
    if drive_type_list:
        filters.append(CarModel.drive_type.ilike(f"%{drive_type_list[0]}%"))
    if engine_cylinder_list:
        filters.append(CarModel.engine_cylinder.ilike(f"%{engine_cylinder_list[0]}%"))
    if vehicle_type_list:
        filters.append(CarModel.vehicle_type.ilike(f"%{vehicle_type_list[0]}%"))
    if body_style_list:
        filters.append(CarModel.body_style.ilike(f"%{body_style_list[0]}%"))
    if auction_name_list:
        filters.append(CarModel.auction_name.ilike(f"%{auction_name_list[0]}%"))
    if recommendation_status_list:
        filters.append(CarModel.recommendation_status == recommendation_status_list[0])

    if predicted_roi_min is not None:
        filters.append(CarModel.predicted_roi >= predicted_roi_min)
    if predicted_roi_max is not None:
        filters.append(CarModel.predicted_roi <= predicted_roi_max)

    if predicted_profit_margin_min is not None:
        filters.append(CarModel.predicted_profit_margin >= predicted_profit_margin_min)
    if predicted_profit_margin_max is not None:
        filters.append(CarModel.predicted_profit_margin <= predicted_profit_margin_max)

    stmt = select(
        CarModel.vehicle,
        CarModel.vin,
        CarModel.owners,
        CarModel.accident_count,
        CarModel.mileage,
        CarModel.engine,
        CarModel.has_keys,
        CarModel.auction_name,
        CarModel.lot,
        CarModel.seller,
        CarModel.location,
        CarModel.date,
        CarModel.current_bid
    )
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.limit(20)

    result = await session.execute(stmt)
    cars = result.fetchall()

    car_list = [
        {
            "vehicle": row[0],
            "vin": row[1],
            "number_of_owners": row[2],
            "accident_count": row[3],
            "mileage": row[4],
            "engine": row[5],
            "has_keys": row[6],
            "auction_name": row[7],
            "lot": row[8],
            "seller": row[9],
            "location": row[10],
            "date": row[11],
            "current_bid": row[12]
        }
        for row in cars
    ]

    if filters and not car_list:
        raise HTTPException(status_code=404, detail="No cars found with specified filters")

    return car_list


@router.get(
    "/top-sellers",
    summary="Top 10 sellers by sold lots",
    description="""
Returns the top 10 sellers ranked by the number of sold lots, filtered by optional vehicle and sale criteria.
...
""",
)
async def get_top_sellers(
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
    session: AsyncSession = Depends(get_db),
):
    state_codes_list = normalize_csv_param(state_codes)
    cities_list = normalize_csv_param(cities)
    auctions_list = normalize_csv_param(auctions)
    vehicle_condition_list = normalize_csv_param(vehicle_condition)
    vehicle_types_list = normalize_csv_param(vehicle_types)
    engine_type_list = normalize_csv_param(engine_type)
    transmission_list = normalize_csv_param(transmission)
    drive_train_list = normalize_csv_param(drive_train)
    cylinder_list = normalize_csv_param(cylinder)
    auction_names_list = normalize_csv_param(auction_names)
    body_style_list = normalize_csv_param(body_style)
    sale_start_date = normalize_date_param(sale_start)
    sale_end_date = normalize_date_param(sale_end)

    filters = []

    if state_codes_list:
        filters.append(CarModel.location.ilike(f"%({','.join(state_codes_list)})%"))
    if cities_list:
        filters.append(CarModel.location.ilike(f"%{','.join(cities_list)}%"))
    if auctions_list:
        filters.append(CarModel.auction.in_(auctions_list))
    if mileage_start is not None:
        filters.append(CarModel.mileage >= mileage_start)
    if mileage_end is not None:
        filters.append(CarModel.mileage <= mileage_end)
    if owners_start is not None:
        filters.append(CarModel.owners >= owners_start)
    if owners_end is not None:
        filters.append(CarModel.owners <= owners_end)
    if accident_start is not None:
        filters.append(CarModel.accident_count >= accident_start)
    if accident_end is not None:
        filters.append(CarModel.accident_count <= accident_end)
    if year_start is not None:
        filters.append(CarModel.year >= year_start)
    if year_end is not None:
        filters.append(CarModel.year <= year_end)
    if vehicle_condition_list:
        filters.append(ConditionAssessmentModel.issue_description.in_(vehicle_condition_list))
    if vehicle_types_list:
        filters.append(CarModel.vehicle_type.in_(vehicle_types_list))
    if make is not None:
        filters.append(CarModel.make.ilike(f"%{make}%"))
    if model is not None:
        filters.append(CarModel.model.ilike(f"%{model}%"))
    if predicted_roi_start is not None:
        filters.append(CarModel.predicted_roi >= predicted_roi_start)
    if predicted_roi_end is not None:
        filters.append(CarModel.predicted_roi <= predicted_roi_end)
    if predicted_profit_margin_start is not None:
        filters.append(CarModel.predicted_profit_margin >= predicted_profit_margin_start)
    if predicted_profit_margin_end is not None:
        filters.append(CarModel.predicted_profit_margin <= predicted_profit_margin_end)
    if engine_type_list:
        filters.append(CarModel.engine.in_(engine_type_list))
    if transmission_list:
        filters.append(CarModel.transmision.in_(transmission_list))
    if drive_train_list:
        filters.append(CarModel.drive_type.in_(drive_train_list))
    if cylinder_list:
        filters.append(CarModel.engine_cylinder.in_(cylinder_list))
    if auction_names_list:
        filters.append(CarModel.auction_name.in_(auction_names_list))
    if body_style_list:
        filters.append(CarModel.body_style.in_(body_style_list))
    if sale_start_date and sale_end_date:
        filters.append(CarSaleHistoryModel.date.between(sale_start_date, sale_end_date))
    elif sale_start_date:
        filters.append(CarSaleHistoryModel.date >= sale_start_date)
    elif sale_end_date:
        filters.append(CarSaleHistoryModel.date <= sale_end_date)

    stmt = (
        select(CarModel.seller.label("Seller Name"), select([CarSaleHistoryModel]).where(CarSaleHistoryModel.car_id == CarModel.id).exists().label("Lots"))
        .join(CarSaleHistoryModel, CarModel.id == CarSaleHistoryModel.car_id)
        .join(ConditionAssessmentModel, CarModel.id == ConditionAssessmentModel.car_id)
        .where(CarSaleHistoryModel.status == "Sold", CarSaleHistoryModel.final_bid.isnot(None))
    )
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.group_by(CarModel.seller).order_by(select([CarSaleHistoryModel]).where(CarSaleHistoryModel.car_id == CarModel.id).exists().desc()).limit(10)

    result = await session.execute(stmt)
    sellers = result.fetchall()

    seller_list = [{"Seller Name": row[0], "Lots": row[1]} for row in sellers]

    if filters and not seller_list:
        raise HTTPException(status_code=404, detail="No sellers found with specified filters")

    return seller_list


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
    session: AsyncSession = Depends(get_db),
):
    state_codes_list = normalize_csv_param(state_codes)
    cities_list = normalize_csv_param(cities)
    auctions_list = normalize_csv_param(auctions)
    vehicle_condition_list = normalize_csv_param(vehicle_condition)
    vehicle_types_list = normalize_csv_param(vehicle_types)
    engine_type_list = normalize_csv_param(engine_type)
    transmission_list = normalize_csv_param(transmission)
    drive_train_list = normalize_csv_param(drive_train)
    cylinder_list = normalize_csv_param(cylinder)
    auction_names_list = normalize_csv_param(auction_names)
    body_style_list = normalize_csv_param(body_style)

    ref_date = reference_date or datetime.utcnow()
    start_date = ref_date - interval_amount * {"day": 1, "week": 7, "month": 30}[interval_unit]

    filters = []

    if state_codes_list:
        filters.append(CarModel.location.ilike(f"%({','.join(state_codes_list)})%"))
    if cities_list:
        filters.append(CarModel.location.ilike(f"%{','.join(cities_list)}%"))
    if auctions_list:
        filters.append(CarModel.auction.in_(auctions_list))
    if mileage_start is not None:
        filters.append(CarModel.mileage >= mileage_start)
    if mileage_end is not None:
        filters.append(CarModel.mileage <= mileage_end)
    if owners_start is not None:
        filters.append(CarModel.owners >= owners_start)
    if owners_end is not None:
        filters.append(CarModel.owners <= owners_end)
    if accident_start is not None:
        filters.append(CarModel.accident_count >= accident_start)
    if accident_end is not None:
        filters.append(CarModel.accident_count <= accident_end)
    if year_start is not None:
        filters.append(CarModel.year >= year_start)
    if year_end is not None:
        filters.append(CarModel.year <= year_end)
    if vehicle_condition_list:
        filters.append(ConditionAssessmentModel.issue_description.in_(vehicle_condition_list))
    if vehicle_types_list:
        filters.append(CarModel.vehicle_type.in_(vehicle_types_list))
    if make is not None:
        filters.append(CarModel.make.ilike(f"%{make}%"))
    if model is not None:
        filters.append(CarModel.model.ilike(f"%{model}%"))
    if predicted_roi_start is not None:
        filters.append(CarModel.predicted_roi >= predicted_roi_start)
    if predicted_roi_end is not None:
        filters.append(CarModel.predicted_roi <= predicted_roi_end)
    if predicted_profit_margin_start is not None:
        filters.append(CarModel.predicted_profit_margin >= predicted_profit_margin_start)
    if predicted_profit_margin_end is not None:
        filters.append(CarModel.predicted_profit_margin <= predicted_profit_margin_end)
    if engine_type_list:
        filters.append(CarModel.engine.in_(engine_type_list))
    if transmission_list:
        filters.append(CarModel.transmision.in_(transmission_list))
    if drive_train_list:
        filters.append(CarModel.drive_type.in_(drive_train_list))
    if cylinder_list:
        filters.append(CarModel.engine_cylinder.in_(cylinder_list))
    if auction_names_list:
        filters.append(CarModel.auction_name.in_(auction_names_list))
    if body_style_list:
        filters.append(CarModel.body_style.in_(body_style_list))
    if sale_start and sale_end:
        filters.append(CarSaleHistoryModel.date.between(sale_start, sale_end))
    elif sale_start:
        filters.append(CarSaleHistoryModel.date >= sale_start)
    elif sale_end:
        filters.append(CarSaleHistoryModel.date <= sale_end)

    stmt = (
        select(
            getattr(CarSaleHistoryModel.date, f"{interval_unit}s")().label("period"),
            select([CarSaleHistoryModel.final_bid]).where(CarSaleHistoryModel.car_id == CarModel.id).avg().label("avg_price")
        )
        .join(CarModel, CarSaleHistoryModel.car_id == CarModel.id)
        .join(ConditionAssessmentModel, CarModel.id == ConditionAssessmentModel.car_id)
        .where(
            CarSaleHistoryModel.status == "Sold",
            CarSaleHistoryModel.final_bid.isnot(None),
            CarSaleHistoryModel.date >= start_date,
            CarSaleHistoryModel.date < ref_date
        )
    )
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.group_by(getattr(CarSaleHistoryModel.date, f"{interval_unit}s")()).order_by("period")

    result = await session.execute(stmt)
    prices = result.fetchall()

    price_list = [{"period": row[0].isoformat(), "avg_price": float(row[1]) if row[1] else 0.0} for row in prices]

    if filters and not price_list:
        raise HTTPException(status_code=404, detail="No sale prices found with specified filters")

    return price_list