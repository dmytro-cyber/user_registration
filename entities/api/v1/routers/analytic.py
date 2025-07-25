import logging
import sys
from datetime import datetime, timedelta, time, timezone
from typing import Literal, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db
from models import CarModel, CarSaleHistoryModel, ConditionAssessmentModel, USZipModel

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
    today = datetime.now(timezone.utc).date()
    today_naive = datetime.combine(today, time.min)
    
    filters.append(CarModel.date >= today_naive)

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
        CarModel.current_bid,
        CarModel.id,
        CarModel.auction
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
            "current_bid": row[12],
            "id": row[13],
            "auction": row[14]
        }
        for row in cars
    ]

    if filters and not car_list:
        raise HTTPException(status_code=404, detail="No cars found with specified filters")

    return car_list


@router.get(
    "/top-sellers",
    summary="Top 10 sellers by sold lots",
    description="Returns the top 10 sellers ranked by the number of sold lots, filtered by optional vehicle and sale criteria.",
)
async def get_top_sellers(
    locations: Optional[str] = Query(None),
    auctions: Optional[str] = Query(None),
    mileage_start: Optional[int] = Query(None),
    mileage_end: Optional[int] = Query(None),
    owners_start: Optional[int] = Query(None),
    owners_end: Optional[int] = Query(None),
    accident_start: Optional[int] = Query(None),
    accident_end: Optional[int] = Query(None),
    year_start: Optional[int] = Query(None),
    year_end: Optional[int] = Query(None),
    vehicle_condition: Optional[str] = Query(None),
    vehicle_types: Optional[str] = Query(None),
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    predicted_roi_start: Optional[float] = Query(None),
    predicted_roi_end: Optional[float] = Query(None),
    predicted_profit_margin_start: Optional[float] = Query(None),
    predicted_profit_margin_end: Optional[float] = Query(None),
    engine_type: Optional[str] = Query(None),
    transmission: Optional[str] = Query(None),
    drive_train: Optional[str] = Query(None),
    cylinder: Optional[str] = Query(None),
    auction_names: Optional[str] = Query(None),
    body_style: Optional[str] = Query(None),
    sale_start: Optional[str] = Query(None),
    sale_end: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    # Нормалізація параметрів
    filters = []
    
    locations_list = normalize_csv_param(locations)
    if not locations_list:
        query = (
            select(USZipModel.copart_name, USZipModel.iaai_name)
            .where(
                or_(
                    USZipModel.copart_name.isnot(None),
                    USZipModel.iaai_name.isnot(None)
                )
            )
            .distinct()
        )
        result = await session.execute(query)
        locations_result = result.all()
        locations_list = {
            name.strip()
            for copart, iaai in locations
            for name in (copart, iaai)
            if name and name.strip()
        }
    filters.append(CarModel.location.in_(locations_list))

    auctions_list = normalize_csv_param(auctions)
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

    vehicle_condition_list = normalize_csv_param(vehicle_condition)
    if vehicle_condition_list:
        filters.append(ConditionAssessmentModel.issue_description.in_(vehicle_condition_list))

    vehicle_types_list = normalize_csv_param(vehicle_types)
    if vehicle_types_list:
        filters.append(CarModel.vehicle_type.in_(vehicle_types_list))

    if make:
        filters.append(CarModel.make.ilike(f"%{make}%"))
    if model:
        filters.append(CarModel.model.ilike(f"%{model}%"))

    if predicted_roi_start is not None:
        filters.append(CarModel.predicted_roi >= predicted_roi_start)
    if predicted_roi_end is not None:
        filters.append(CarModel.predicted_roi <= predicted_roi_end)

    if predicted_profit_margin_start is not None:
        filters.append(CarModel.predicted_profit_margin >= predicted_profit_margin_start)
    if predicted_profit_margin_end is not None:
        filters.append(CarModel.predicted_profit_margin <= predicted_profit_margin_end)

    engine_type_list = normalize_csv_param(engine_type)
    if engine_type_list:
        filters.append(CarModel.engine.in_(engine_type_list))

    transmission_list = normalize_csv_param(transmission)
    if transmission_list:
        filters.append(CarModel.transmision.in_(transmission_list))

    drive_train_list = normalize_csv_param(drive_train)
    if drive_train_list:
        filters.append(CarModel.drive_type.in_(drive_train_list))

    cylinder_list = [int(value) for value in normalize_csv_param(cylinder)]
    if cylinder_list:
        filters.append(CarModel.engine_cylinder.in_(cylinder_list))

    auction_names_list = normalize_csv_param(auction_names)
    if auction_names_list:
        filters.append(CarModel.auction_name.in_(auction_names_list))

    body_style_list = normalize_csv_param(body_style)
    if body_style_list:
        filters.append(CarModel.body_style.in_(body_style_list))

    sale_start_date = normalize_date_param(sale_start)
    sale_end_date = normalize_date_param(sale_end)
    if sale_start_date and sale_end_date:
        filters.append(CarSaleHistoryModel.date.between(sale_start_date, sale_end_date))
    elif sale_start_date:
        filters.append(CarSaleHistoryModel.date >= sale_start_date)
    elif sale_end_date:
        filters.append(CarSaleHistoryModel.date <= sale_end_date)

    # Основний запит
    stmt = (
        select(
            CarModel.seller.label("Seller Name"),
            func.count(CarSaleHistoryModel.id).label("Lots")
        )
        .join(CarSaleHistoryModel, CarModel.id == CarSaleHistoryModel.car_id)
        .outerjoin(ConditionAssessmentModel, CarModel.id == ConditionAssessmentModel.car_id)
        .where(
            CarSaleHistoryModel.status == "Sold",
            CarSaleHistoryModel.final_bid.isnot(None),
            *filters
        )
        .group_by(CarModel.seller)
        .order_by(func.count(CarSaleHistoryModel.id).desc())
        .limit(10)
    )

    result = await session.execute(stmt)
    sellers = result.fetchall()

    if filters and not sellers:
        raise HTTPException(status_code=404, detail="No sellers found with specified filters")

    return [{"Seller Name": row[0], "Lots": row[1]} for row in sellers]



@router.get(
    "/analytics/sale-prices",
    summary="Average Sale Price Over Time",
    tags=["Analytics"],
    description="""
Returns the average final bid prices grouped by the specified time interval (day, week, or month) over a given period.
""",
)
async def get_avg_sale_prices(
    interval_unit: Literal["day", "week", "month"] = Query("week"),
    interval_amount: int = Query(12, ge=1),
    reference_date: Optional[datetime] = Query(None),
    locations: Optional[str] = Query(None),
    auctions: Optional[str] = Query(None),
    mileage_start: Optional[int] = Query(None),
    mileage_end: Optional[int] = Query(None),
    owners_start: Optional[int] = Query(None),
    owners_end: Optional[int] = Query(None),
    accident_start: Optional[int] = Query(None),
    accident_end: Optional[int] = Query(None),
    year_start: Optional[int] = Query(None),
    year_end: Optional[int] = Query(None),
    vehicle_condition: Optional[str] = Query(None),
    vehicle_types: Optional[str] = Query(None),
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    predicted_roi_start: Optional[float] = Query(None),
    predicted_roi_end: Optional[float] = Query(None),
    predicted_profit_margin_start: Optional[float] = Query(None),
    predicted_profit_margin_end: Optional[float] = Query(None),
    engine_type: Optional[str] = Query(None),
    transmission: Optional[str] = Query(None),
    drive_train: Optional[str] = Query(None),
    cylinder: Optional[str] = Query(None),
    auction_names: Optional[str] = Query(None),
    body_style: Optional[str] = Query(None),
    sale_start: Optional[datetime] = Query(None),
    sale_end: Optional[datetime] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    # Normalize parameters
    def csv(param: Optional[str]) -> Optional[list[str]]:
        return [x.strip() for x in param.split(",") if x.strip()] if param else None

    location_list = normalize_csv_param(locations)
    auctions_list = normalize_csv_param(auctions)
    vehicle_condition_list = normalize_csv_param(vehicle_condition)
    vehicle_types_list = normalize_csv_param(vehicle_types)
    engine_type_list = normalize_csv_param(engine_type)
    transmission_list = normalize_csv_param(transmission)
    drive_train_list = normalize_csv_param(drive_train)
    cylinder_list = [int(value) for value in normalize_csv_param(cylinder)]
    auction_names_list = normalize_csv_param(auction_names)
    body_style_list = normalize_csv_param(body_style)

    ref_date = reference_date or datetime.utcnow()
    start_date = ref_date - timedelta(days=interval_amount * {"day": 1, "week": 7, "month": 30}[interval_unit])

    filters = [
        CarSaleHistoryModel.status == "Sold",
        CarSaleHistoryModel.final_bid.isnot(None),
        CarSaleHistoryModel.date >= start_date,
        CarSaleHistoryModel.date <= ref_date,
    ]

    if location_list:
        filters.append(CarModel.location.in_(location_list))
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
    if make:
        filters.append(CarModel.make.ilike(f"%{make}%"))
    if model:
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

    # Group by interval (day/week/month)
    interval_expr = func.date_trunc(interval_unit, CarSaleHistoryModel.date).label("period")

    stmt = (
        select(
            interval_expr,
            func.avg(CarSaleHistoryModel.final_bid).label("avg_price")
        )
        .join(CarModel, CarSaleHistoryModel.car_id == CarModel.id)
        .outerjoin(ConditionAssessmentModel, CarModel.id == ConditionAssessmentModel.car_id)
        .where(and_(*filters))
        .group_by(interval_expr)
        .order_by(interval_expr)
    )

    result = await session.execute(stmt)
    rows = result.fetchall()

    data = [
        {
            "period": row.period.date().isoformat(),
            "avg_price": float(row.avg_price) if row.avg_price else 0.0
        }
        for row in rows
    ]

    if filters and not data:
        raise HTTPException(status_code=404, detail="No sale prices found with specified filters")

    return data



@router.get("/locations-by-lots")
async def get_locations_with_coords(
    auctions: Optional[List[str]] = Query(None),
    year_start: Optional[int] = Query(None),
    year_end: Optional[int] = Query(None),
    mileage_start: Optional[int] = Query(None),
    mileage_end: Optional[int] = Query(None),
    owners_start: Optional[int] = Query(None),
    owners_end: Optional[int] = Query(None),
    accident_start: Optional[int] = Query(None),
    accident_end: Optional[int] = Query(None),
    vehicle_condition: Optional[List[str]] = Query(None),
    vehicle_types: Optional[List[str]] = Query(None),
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    predicted_roi_start: Optional[float] = Query(None),
    predicted_roi_end: Optional[float] = Query(None),
    predicted_profit_margin_start: Optional[float] = Query(None),
    predicted_profit_margin_end: Optional[float] = Query(None),
    engine_type: Optional[List[str]] = Query(None),
    transmission: Optional[List[str]] = Query(None),
    drive_train: Optional[List[str]] = Query(None),
    cylinder: Optional[List[int]] = Query(None),
    auction_names: Optional[List[str]] = Query(None),
    body_style: Optional[List[str]] = Query(None),
    sale_start: Optional[str] = Query(None),
    sale_end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    today = datetime.now(timezone.utc).date()
    today_naive = datetime.combine(today, time.min)
    query = (
        select(CarModel.location, CarModel.auction, func.count().label("lots"))
        .outerjoin(ConditionAssessmentModel, ConditionAssessmentModel.car_id == CarModel.id)
        .filter(CarModel.date >= today_naive)
    )

    if auctions:
        query = query.filter(CarModel.auction.in_(auctions))
    if year_start and year_end:
        query = query.filter(CarModel.year.between(year_start, year_end))
    if mileage_start and mileage_end:
        query = query.filter(CarModel.mileage.between(mileage_start, mileage_end))
    if owners_start and owners_end:
        query = query.filter(CarModel.owners.between(owners_start, owners_end))
    if accident_start and accident_end:
        query = query.filter(CarModel.accident_count.between(accident_start, accident_end))
    if vehicle_condition:
        query = query.filter(ConditionAssessmentModel.issue_description.in_(vehicle_condition))
    if vehicle_types:
        query = query.filter(CarModel.vehicle_type.in_(vehicle_types))
    if make:
        query = query.filter(CarModel.make == make)
    if model:
        query = query.filter(CarModel.model == model)
    if predicted_roi_start and predicted_roi_end:
        query = query.filter(CarModel.predicted_roi.between(predicted_roi_start, predicted_roi_end))
    if predicted_profit_margin_start and predicted_profit_margin_end:
        query = query.filter(CarModel.predicted_profit_margin.between(predicted_profit_margin_start, predicted_profit_margin_end))
    if engine_type:
        query = query.filter(CarModel.engine.in_(engine_type))
    if transmission:
        query = query.filter(CarModel.transmision.in_(transmission))
    if drive_train:
        query = query.filter(CarModel.drive_type.in_(drive_train))
    if cylinder:
        query = query.filter(CarModel.engine_cylinder.in_(cylinder))
    if auction_names:
        query = query.filter(CarModel.auction_name.in_(auction_names))
    if body_style:
        query = query.filter(CarModel.body_style.in_(body_style))
    if sale_start and sale_end:
        query = query.filter(CarSaleHistoryModel.date.between(sale_start, sale_end))

    query = query.group_by(CarModel.location, CarModel.auction)

    result = await db.execute(query)
    raw_data = result.all()
    logger.info(f"RAWWWWWWWWWWW -->>>> {raw_data}")

    locations = set([row[0].lower() for row in raw_data if row[0]])
    if not locations:
        return []

    coord_query = select(USZipModel).where(
        or_(func.lower(USZipModel.copart_name).in_(locations), func.lower(USZipModel.iaai_name).in_(locations))
    )
    coords_result = await db.execute(coord_query)
    zip_map = {}
    for z in coords_result.scalars():
        if z.copart_name:
            zip_map[z.copart_name.lower()] = (z.lat, z.lng)
        if z.iaai_name:
            zip_map[z.iaai_name.lower()] = (z.lat, z.lng)

    response = []
    for location, auction, lots in raw_data:
        key = location.lower()
        if key in zip_map:
            lat, lng = zip_map[key]
            response.append({
                "location": location,
                "auction": auction,
                "lots": lots,
                "lat": lat,
                "lng": lng,
            })

    return response