import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, time, timezone
from itertools import chain
from typing import Literal, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

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
    sale_start: Optional[str] = Query(None),
    sale_end: Optional[str] = Query(None),
    auctions: Optional[str] = Query(None),
    vin: Optional[str] = Query(
        None,
        description="If provided, the filters 'make', 'model', 'year_start', and 'year_end' will be ignored. Only the vehicle with this VIN will be used as a reference."
    ),
    session: AsyncSession = Depends(get_db),
):
    logger.debug("Entering get_filtered_cars endpoint")
    engine_list = normalize_csv_param(engine)
    transmision_list = normalize_csv_param(transmision)
    drive_type_list = normalize_csv_param(drive_type)
    engine_cylinder_list = [int(c) for c in normalize_csv_param(engine_cylinder)]
    vehicle_type_list = normalize_csv_param(vehicle_type)
    body_style_list = normalize_csv_param(body_style)
    auction_name_list = normalize_csv_param(auction_name)
    recommendation_status_list = normalize_csv_param(recommendation_status)
    auctions_list = normalize_csv_param(auctions)

    filters = []
    today = datetime.now(timezone.utc).date()
    today_naive = datetime.combine(today, time.min)
    filters.append(CarModel.date >= today_naive)

    if vin is not None:
        if len(vin) != 17:
            logger.error(f"Invalid VIN length: {len(vin)}")
            raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters long.")

        query = select(CarModel).where(CarModel.vin == vin)
        result = await session.execute(query)
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            logger.warning(f"Car with VIN {vin} not found")
            raise HTTPException(status_code=404, detail=f"Car with VIN {vin} not found.")

        make = vehicle.make
        model = vehicle.model
        year_start = vehicle.year
        year_end = vehicle.year

    sale_start_date = normalize_date_param(sale_start)
    sale_end_date = normalize_date_param(sale_end)

    if auctions_list:
        filters.append(CarModel.auction.in_(auctions_list))
    if sale_start_date is not None:
        filters.append(CarModel.date >= sale_start_date)
    if sale_end_date is not None:
        filters.append(CarModel.date <= sale_end_date)
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
    if make:
        filters.append(CarModel.make == make)
    if model:
        filters.append(CarModel.model == model)
    if engine_list:
        filters.append(CarModel.engine.in_(engine_list))
    if transmision_list:
        filters.append(CarModel.transmision.in_(transmision_list))
    if drive_type_list:
        filters.append(CarModel.drive_type.in_(drive_type_list))
    if engine_cylinder_list:
        filters.append(CarModel.engine_cylinder.in_(engine_cylinder_list))
    if vehicle_type_list:
        filters.append(CarModel.vehicle_type.in_(vehicle_type_list))
    if body_style_list:
        filters.append(CarModel.body_style.in_(body_style_list))
    if auction_name_list:
        filters.append(CarModel.auction_name.in_(auction_name_list))
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

    query = select(
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
        query = query.filter(and_(*filters))
    query = query.limit(20)

    result = await session.execute(query)
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
        logger.warning("No cars found with specified filters")
        raise HTTPException(status_code=404, detail="No cars found with specified filters")

    logger.debug(f"Returning {len(car_list)} cars")
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
    vin: Optional[str] = Query(
        None,
        description="If provided, the filters 'make', 'model', 'year_start', and 'year_end' will be ignored. Only the vehicle with this VIN will be used as a reference."
    ),
    session: AsyncSession = Depends(get_db),
):
    logger.debug("Entering get_top_sellers endpoint")
    filters = []
    if vin is not None:
        if len(vin) != 17:
            logger.error(f"Invalid VIN length: {len(vin)}")
            raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters long.")

        query = select(CarModel).where(CarModel.vin == vin)
        result = await session.execute(query)
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            logger.warning(f"Car with VIN {vin} not found")
            raise HTTPException(status_code=404, detail=f"Car with VIN {vin} not found.")

        make = vehicle.make
        model = vehicle.model
        year_start = vehicle.year
        year_end = vehicle.year

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
            for copart, iaai in locations_result
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
        filters.append(CarSaleHistoryModel.date >= datetime.combine(sale_start_date, time.min))
    elif sale_end_date:
        filters.append(CarSaleHistoryModel.date <= datetime.combine(sale_end_date, time.max))

    query = (
        select(
            CarModel.seller.label("Seller Name"),
            func.count(CarSaleHistoryModel.id).label("Lots")
        )
        .join(CarSaleHistoryModel, CarModel.id == CarSaleHistoryModel.car_id)
        .outerjoin(ConditionAssessmentModel, CarModel.id == ConditionAssessmentModel.car_id)
    )
    if filters:
        query = query.filter(
            CarSaleHistoryModel.status == "Sold",
            CarSaleHistoryModel.final_bid.isnot(None),
            and_(*filters)
        )
    query = query.group_by(CarModel.seller).order_by(func.count(CarSaleHistoryModel.id).desc()).limit(10)

    result = await session.execute(query)
    sellers = result.fetchall()

    if filters and not sellers:
        logger.warning("No sellers found with specified filters")
        raise HTTPException(status_code=404, detail="No sellers found with specified filters")

    response = [{"Seller Name": row[0], "Lots": row[1]} for row in sellers]
    logger.debug(f"Returning {len(response)} top sellers")
    return response


@router.get(
    "/sale-prices",
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
    vin: Optional[str] = Query(
        None,
        description="If provided, the filters 'make', 'model', 'year_start', and 'year_end' will be ignored. Only the vehicle with this VIN will be used as a reference."
    ),
    session: AsyncSession = Depends(get_db),
):
    logger.debug("Entering get_avg_sale_prices endpoint")
    def csv(param: Optional[str]) -> Optional[list[str]]:
        return [x.strip() for x in param.split(",") if x.strip()] if param else None

    if vin is not None:
        if len(vin) != 17:
            logger.error(f"Invalid VIN length: {len(vin)}")
            raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters long.")

        query = select(CarModel).where(CarModel.vin == vin)
        result = await session.execute(query)
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            logger.warning(f"Car with VIN {vin} not found")
            raise HTTPException(status_code=404, detail=f"Car with VIN {vin} not found.")

        make = vehicle.make
        model = vehicle.model
        year_start = vehicle.year
        year_end = vehicle.year

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

    query = (
        select(
            func.date_trunc(interval_unit, CarSaleHistoryModel.date).label("period"),
            func.avg(CarSaleHistoryModel.final_bid).label("avg_price")
        )
        .join(CarModel, CarSaleHistoryModel.car_id == CarModel.id)
        .outerjoin(ConditionAssessmentModel, CarModel.id == ConditionAssessmentModel.car_id)
    )
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

    if filters:
        query = query.filter(and_(*filters))
    query = query.group_by(func.date_trunc(interval_unit, CarSaleHistoryModel.date)).order_by("period")

    result = await session.execute(query)
    rows = result.fetchall()

    data = [
        {
            "period": row.period.date().isoformat(),
            "avg_price": float(row.avg_price) if row.avg_price else 0.0
        }
        for row in rows
    ]

    if filters and not data:
        logger.warning("No sale prices found with specified filters")
        raise HTTPException(status_code=404, detail="No sale prices found with specified filters")

    logger.debug(f"Returning {len(data)} sale price records")
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
    vin: Optional[str] = Query(
        None,
        description="If provided, the filters 'make', 'model', 'year_start', and 'year_end' will be ignored. Only the vehicle with this VIN will be used as a reference."
    ),
    db: AsyncSession = Depends(get_db),
):
    logger.debug("Entering get_locations_with_coords endpoint")
    if vin is not None:
        if len(vin) != 17:
            logger.error(f"Invalid VIN length: {len(vin)}")
            raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters long.")

        query = select(CarModel).where(CarModel.vin == vin)
        result = await db.execute(query)
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            logger.warning(f"Car with VIN {vin} not found")
            raise HTTPException(status_code=404, detail=f"Car with VIN {vin} not found.")

        make = vehicle.make
        model = vehicle.model
        year_start = vehicle.year
        year_end = vehicle.year

    today = datetime.now(timezone.utc).date()
    today_naive = datetime.combine(today, time.min)
    query = (
        select(CarModel.location, CarModel.auction, func.count().label("lots"))
        .outerjoin(ConditionAssessmentModel, ConditionAssessmentModel.car_id == CarModel.id)
    )
    query = query.filter(CarModel.date >= today_naive)

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
    if sale_start:
        sale_start = normalize_date_param(sale_start)
        query = query.filter(CarSaleHistoryModel.date >= sale_start)
    if sale_end:
        sale_end = normalize_date_param(sale_end)
        query = query.filter(CarSaleHistoryModel.date <= sale_end)

    query = query.group_by(CarModel.location, CarModel.auction)

    result = await db.execute(query)
    raw_data = result.all()

    locations = set([row[0].lower() for row in raw_data if row[0]])
    if not locations:
        logger.debug("No locations found")
        return []

    coord_query = select(USZipModel).where(
        or_(
            func.lower(USZipModel.copart_name).in_(locations),
            func.lower(USZipModel.iaai_name).in_(locations),
        )
    )
    coords_result = await db.execute(coord_query)
    zip_map = {}
    state_map = {}
    for z in coords_result.scalars():
        if z.copart_name:
            zip_map[z.copart_name.lower()] = (z.lat, z.lng)
            state_map[z.copart_name.lower()] = z.state_id
        if z.iaai_name:
            zip_map[z.iaai_name.lower()] = (z.lat, z.lng)
            state_map[z.iaai_name.lower()] = z.state_id

    response = {
        "by_location": [],
        "by_state": [],
        "by_state_and_auction": []
    }

    state_agg = defaultdict(int)
    state_auction_agg = defaultdict(lambda: defaultdict(int))

    for location, auction, lots in raw_data:
        key = location.lower()
        if key in zip_map:
            lat, lng = zip_map[key]
            state = state_map.get(key)
            response["by_location"].append({
                "location": location,
                "auction": auction,
                "lots": lots,
                "lat": lat,
                "lng": lng,
                "state": state,
            })
            if state:
                state_agg[state] += lots
                state_auction_agg[state][auction] += lots

    response["by_state"] = [
        {"state": state, "lots": lots}
        for state, lots in state_agg.items()
    ]

    response["by_state_and_auction"] = [
        {"state": state, "auction": auction, "lots": lots}
        for state, auctions in state_auction_agg.items()
        for auction, lots in auctions.items()
    ]

    logger.debug(f"Returning {len(response['by_location'])} locations")
    return response


@router.get("/avg-final-bid-by-location")
async def avg_final_bid_by_location(
    auctions: Optional[List[str]] = Query(default=None),
    mileage_start: Optional[int] = None,
    mileage_end: Optional[int] = None,
    owners_start: Optional[int] = None,
    owners_end: Optional[int] = None,
    accident_start: Optional[int] = None,
    accident_end: Optional[int] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    vehicle_condition: Optional[List[str]] = Query(default=None),
    vehicle_types: Optional[List[str]] = Query(default=None),
    make: Optional[str] = None,
    model: Optional[str] = None,
    predicted_roi_start: Optional[float] = None,
    predicted_roi_end: Optional[float] = None,
    predicted_profit_margin_start: Optional[float] = None,
    predicted_profit_margin_end: Optional[float] = None,
    engine_type: Optional[List[str]] = Query(default=None),
    transmission: Optional[List[str]] = Query(default=None),
    drive_train: Optional[List[str]] = Query(default=None),
    cylinder: Optional[List[str]] = Query(default=None),
    auction_names: Optional[List[str]] = Query(default=None),
    body_style: Optional[List[str]] = Query(default=None),
    sale_start: Optional[str] = None,
    sale_end: Optional[str] = None,
    vin: Optional[str] = Query(
        None,
        description="If provided, the filters 'make', 'model', 'year_start', and 'year_end' will be ignored. Only the vehicle with this VIN will be used as a reference."
    ),
    session: AsyncSession = Depends(get_db),
):
    logger.debug("Entering avg_final_bid_by_location endpoint")
    if vin is not None:
        if len(vin) != 17:
            logger.error(f"Invalid VIN length: {len(vin)}")
            raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters long.")

        query = select(CarModel).where(CarModel.vin == vin)
        result = await session.execute(query)
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            logger.warning(f"Car with VIN {vin} not found")
            raise HTTPException(status_code=404, detail=f"Car with VIN {vin} not found.")

        make = vehicle.make
        model = vehicle.model
        year_start = vehicle.year
        year_end = vehicle.year

    sale_start_dt = datetime.fromisoformat(sale_start) if sale_start else None
    sale_end_dt = datetime.fromisoformat(sale_end) if sale_end else None

    us_zips_stmt = select(
        USZipModel.copart_name,
        USZipModel.iaai_name,
        USZipModel.lat,
        USZipModel.lng
    ).where(
        or_(USZipModel.copart_name.isnot(None), USZipModel.iaai_name.isnot(None)),
        USZipModel.lat.isnot(None),
        USZipModel.lng.isnot(None)
    )
    us_zips_result = await session.execute(us_zips_stmt)
    zip_coords = us_zips_result.all()

    location_to_coords = {
        name: {"lat": z.lat, "lng": z.lng, "auction": auction}
        for z in zip_coords
        for name, auction in chain(
            [(z.copart_name, "copart")] if z.copart_name else [],
            [(z.iaai_name, "iaai")] if z.iaai_name else []
        )
    }

    query = (
        select(
            CarModel.location,
            CarModel.auction,
            func.avg(CarSaleHistoryModel.final_bid).label("average_final_bid")
        )
        .join(CarSaleHistoryModel, CarSaleHistoryModel.car_id == CarModel.id)
        .join(ConditionAssessmentModel, ConditionAssessmentModel.car_id == CarModel.id)
    )
    filters = [
        CarModel.seller.isnot(None),
        CarSaleHistoryModel.final_bid.isnot(None),
        CarSaleHistoryModel.status == 'Sold'
    ]
    if auctions:
        filters.append(CarModel.auction.in_(auctions))
    if mileage_start is not None and mileage_end is not None:
        filters.append(CarModel.mileage.between(mileage_start, mileage_end))
    if owners_start is not None and owners_end is not None:
        filters.append(CarModel.owners.between(owners_start, owners_end))
    if accident_start is not None and accident_end is not None:
        filters.append(CarModel.accident_count.between(accident_start, accident_end))
    if year_start is not None and year_end is not None:
        filters.append(CarModel.year.between(year_start, year_end))
    if vehicle_condition:
        filters.append(ConditionAssessmentModel.issue_description.in_(vehicle_condition))
    if vehicle_types:
        filters.append(CarModel.vehicle_type.in_(vehicle_types))
    if make:
        filters.append(CarModel.make == make)
    if model:
        filters.append(CarModel.model == model)
    if predicted_roi_start is not None and predicted_roi_end is not None:
        filters.append(CarModel.predicted_roi.between(predicted_roi_start, predicted_roi_end))
    if predicted_profit_margin_start is not None and predicted_profit_margin_end is not None:
        filters.append(CarModel.predicted_profit_margin.between(predicted_profit_margin_start, predicted_profit_margin_end))
    if engine_type:
        filters.append(CarModel.engine.in_(engine_type))
    if transmission:
        filters.append(CarModel.transmision.in_(transmission))
    if drive_train:
        filters.append(CarModel.drive_type.in_(drive_train))
    if cylinder:
        filters.append(CarModel.engine_cylinder.in_(cylinder))
    if auction_names:
        filters.append(CarModel.auction_name.in_(auction_names))
    if body_style:
        filters.append(CarModel.body_style.in_(body_style))
    if sale_start_dt and sale_end_dt:
        filters.append(CarSaleHistoryModel.date.between(sale_start_dt, sale_end_dt))
    elif sale_start_dt:
        filters.append(CarSaleHistoryModel.date >= sale_start_dt)
    elif sale_end_dt:
        filters.append(CarSaleHistoryModel.date <= sale_end_dt)

    if filters:
        query = query.filter(and_(*filters))
    query = query.group_by(CarModel.location, CarModel.auction).order_by(func.avg(CarSaleHistoryModel.final_bid).desc())

    car_result = await session.execute(query)
    raw_data = car_result.all()

    response = []
    for row in raw_data:
        location_name = row.location
        coords = location_to_coords.get(location_name)
        if coords:
            response.append({
                "location": location_name,
                "lat": coords["lat"],
                "lng": coords["lng"],
                "auction": row.auction,
                "average_final_bid": round(row.average_final_bid or 0)
            })

    logger.debug(f"Returning {len(response)} locations with average final bids")
    return response


@router.get("/volumes")
async def get_sales_summary(
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
    cylinder: Optional[List[int]] = Query(None),
    auction_names: Optional[List[str]] = Query(None),
    body_style: Optional[List[str]] = Query(None),
    sale_start: Optional[str] = None,
    sale_end: Optional[str] = None,
    vin: Optional[str] = Query(
        None,
        description="If provided, the filters 'make', 'model', 'year_start', and 'year_end' will be ignored. Only the vehicle with this VIN will be used as a reference."
    ),
):
    logger.debug("Entering get_sales_summary endpoint")
    if vin is not None:
        if len(vin) != 17:
            logger.error(f"Invalid VIN length: {len(vin)}")
            raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters long.")

        query = select(CarModel).where(CarModel.vin == vin)
        result = await db.execute(query)
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            logger.warning(f"Car with VIN {vin} not found")
            raise HTTPException(status_code=404, detail=f"Car with VIN {vin} not found.")

        make = vehicle.make
        model = vehicle.model
        year_start = vehicle.year
        year_end = vehicle.year

    query = select(CarModel).options(
        joinedload(CarModel.sales_history),
        joinedload(CarModel.condition_assessments)
    ).join(CarModel.sales_history).join(CarModel.condition_assessments)

    filters = [
        CarSaleHistoryModel.status == 'Sold',
        CarSaleHistoryModel.final_bid.isnot(None),
        CarSaleHistoryModel.source != 'Unknown',
        CarModel.seller.isnot(None),
    ]
    if mileage_start is not None and mileage_end is not None:
        filters.append(CarModel.mileage.between(mileage_start, mileage_end))
    if owners_start is not None and owners_end is not None:
        filters.append(CarModel.owners.between(owners_start, owners_end))
    if accident_start is not None and accident_end is not None:
        filters.append(CarModel.accident_count.between(accident_start, accident_end))
    if year_start is not None and year_end is not None:
        filters.append(CarModel.year.between(year_start, year_end))
    if vehicle_condition:
        filters.append(ConditionAssessmentModel.issue_description.in_(vehicle_condition))
    if vehicle_types:
        filters.append(CarModel.vehicle_type.in_(vehicle_types))
    if make:
        filters.append(CarModel.make == make)
    if model:
        filters.append(CarModel.model == model)
    if predicted_roi_start is not None and predicted_roi_end is not None:
        filters.append(CarModel.predicted_roi.between(predicted_roi_start, predicted_roi_end))
    if predicted_profit_margin_start is not None and predicted_profit_margin_end is not None:
        filters.append(CarModel.predicted_profit_margin.between(predicted_profit_margin_start, predicted_profit_margin_end))
    if engine_type:
        filters.append(CarModel.engine.in_(engine_type))
    if transmission:
        filters.append(CarModel.transmision.in_(transmission))
    if drive_train:
        filters.append(CarModel.drive_type.in_(drive_train))
    if cylinder:
        filters.append(CarModel.engine_cylinder.in_(cylinder))
    if auction_names:
        filters.append(CarModel.auction_name.in_(auction_names))
    if body_style:
        filters.append(CarModel.body_style.in_(body_style))
    if sale_start:
        sale_start = normalize_date_param(sale_start)
        filters.append(CarSaleHistoryModel.date >= sale_start)
    if sale_end:
        sale_end = normalize_date_param(sale_end)
        filters.append(CarSaleHistoryModel.date <= sale_end)

    query = query.filter(and_(*filters))

    result = await db.execute(query)
    cars = result.scalars().unique().all()

    total_sales = 0.0
    source_sales = {}

    for car in cars:
        for sale in car.sales_history:
            if sale.status != 'Sold' or sale.final_bid is None or sale.source == 'Unknown':
                continue
            total_sales += sale.final_bid
            source_sales[sale.source] = source_sales.get(sale.source, 0.0) + sale.final_bid

    response = {
        "total_sales": round(total_sales),
        "sales_by_source": [
            {
                "source": source,
                "amount": round(amount),
                "percent": round(amount * 100 / total_sales, 2) if total_sales else 0.0
            }
            for source, amount in sorted(source_sales.items(), key=lambda x: x[1], reverse=True)
        ]
    }
    logger.debug(f"Returning sales summary with total: {total_sales}")
    return response


@router.get("/sales-summary")
async def get_sales_summary(
    db: AsyncSession = Depends(get_db),
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
    vin: Optional[str] = Query(
        None,
        description="If provided, the filters 'make', 'model', 'year_start', and 'year_end' will be ignored. Only the vehicle with this VIN will be used as a reference."
    ),
):
    logger.debug("Entering get_sales_summary endpoint")
    filters = []
    if vin is not None:
        if len(vin) != 17:
            logger.error(f"Invalid VIN length: {len(vin)}")
            raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters long.")

        query = select(CarModel).where(CarModel.vin == vin)
        result = await db.execute(query)
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            logger.warning(f"Car with VIN {vin} not found")
            raise HTTPException(status_code=404, detail=f"Car with VIN {vin} not found.")

        make = vehicle.make
        model = vehicle.model
        year_start = vehicle.year
        year_end = vehicle.year

    if auctions:
        filters.append(CarModel.auction_name.in_(auctions))
    if year_start is not None:
        filters.append(CarModel.year >= year_start)
    if year_end is not None:
        filters.append(CarModel.year <= year_end)
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
    if vehicle_condition:
        filters.append(CarModel.recommendation_status.in_(vehicle_condition))
    if vehicle_types:
        filters.append(CarModel.vehicle_type.in_(vehicle_types))
    if make:
        filters.append(CarModel.make == make)
    if model:
        filters.append(CarModel.model == model)
    if predicted_roi_start is not None:
        filters.append(CarModel.predicted_roi >= predicted_roi_start)
    if predicted_roi_end is not None:
        filters.append(CarModel.predicted_roi <= predicted_roi_end)
    if predicted_profit_margin_start is not None:
        filters.append(CarModel.predicted_profit_margin >= predicted_profit_margin_start)
    if predicted_profit_margin_end is not None:
        filters.append(CarModel.predicted_profit_margin <= predicted_profit_margin_end)
    if engine_type:
        filters.append(CarModel.engine.in_(engine_type))
    if transmission:
        filters.append(CarModel.transmision.in_(transmission))
    if drive_train:
        filters.append(CarModel.drive_type.in_(drive_train))
    if cylinder:
        filters.append(CarModel.engine_cylinder.in_(cylinder))

    query = (
        select(
            CarSaleHistoryModel.status,
            func.count(CarSaleHistoryModel.id).label("count")
        )
        .join(CarModel, CarModel.id == CarSaleHistoryModel.car_id)
    )
    if filters:
        query = query.filter(and_(*filters))
    query = query.group_by(CarSaleHistoryModel.status)

    results = (await db.execute(query)).all()
    total_query = select(func.count(CarSaleHistoryModel.id)).join(CarModel).filter(and_(*filters))
    total = (await db.execute(total_query)).scalar_one()

    breakdown = [
        {
            "status": status,
            "count": count,
            "percentage": round((count / total) * 100, 2) if total else 0.0
        }
        for status, count in results
    ]

    response = {
        "total": total,
        "breakdown": breakdown
    }
    logger.debug(f"Returning sales summary with total: {total}")
    return response