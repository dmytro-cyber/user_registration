import logging
from datetime import datetime, time, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import and_, asc, case, delete, desc, func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.sql import over

from core.setup import match_and_update_location
from models.user import UserModel, UserRoleEnum, user_likes
from models.vehicle import (
    CarInventoryModel,
    CarInventoryStatus,
    CarModel,
    CarSaleHistoryModel,
    CarStatus,
    ConditionAssessmentModel,
    FeeModel,
    HistoryModel,
    PartModel,
    PhotoModel,
    RecommendationStatus,
    USZipModel,
)
from ordering_constr import ORDERING_MAP
from schemas.vehicle import CarCreateSchema

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def save_sale_history(sale_history_data: List[CarCreateSchema], car_id: int, db: AsyncSession) -> None:
    """Save sales history for a vehicle."""
    if len(sale_history_data) >= 4:
        logger.debug(
            f"More than 3 sales history records provided for car ID {car_id}. Car will not be recomendet for purchase."
        )
        car = await get_vehicle_by_id(db, car_id)
        car.recomendation_status = RecommendationStatus.NOT_RECOMMENDED
        if not car.recommendation_status_reasons:
            car.recommendation_status_reasons = f"sales at auction in the last 3 years: {len(sale_history_data)};"
        else:
            car.recommendation_status_reasons += f"sales at auction in the last 3 years: {len(sale_history_data)};"
        db.add(car)
        await db.flush()
    for history_data in sale_history_data:
        sales_history = CarSaleHistoryModel(**history_data.dict(), car_id=car_id)
        if not sales_history.source:
            sales_history.source = "Unknown"
        db.add(sales_history)
        await db.commit()


async def save_vehicle_with_photos(vehicle_data: CarCreateSchema, db: AsyncSession) -> bool:
    """Save a single vehicle and its photos. Update all fields and photos if vehicle already exists."""
    try:
        existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin, 1)
        if existing_vehicle:
            logger.info(f"Vehicle with VIN {vehicle_data.vin} already exists. Updating data...")

            for field, value in vehicle_data.dict(
                exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}
            ).items():
                setattr(existing_vehicle, field, value)
                if field == "fuel_type" and value not in ["Gasoline", "Flexible Fuel", "Unknown"]:
                    existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                    if not existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons = f"{value};"
                    else:
                        existing_vehicle.recommendation_status_reasons += f"{value};"
                if field == "transmision" and value != "Automatic":
                    existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                    if not existing_vehicle.recommendation_status_reasons:
                        existing_vehicle.recommendation_status_reasons = f"{value};"
                    else:
                        existing_vehicle.recommendation_status_reasons += f"{value};"

            existing_photo_urls = {p.url for p in existing_vehicle.photos}
            new_photos = []

            if vehicle_data.photos:
                for photo_data in vehicle_data.photos:
                    if photo_data.url not in existing_photo_urls:
                        new_photos.append(PhotoModel(url=photo_data.url, car_id=existing_vehicle.id, is_hd=False))

            if vehicle_data.photos_hd:
                for photo_data_hd in vehicle_data.photos_hd:
                    if photo_data_hd.url not in existing_photo_urls:
                        new_photos.append(PhotoModel(url=photo_data_hd.url, car_id=existing_vehicle.id, is_hd=True))

            if new_photos:
                db.add_all(new_photos)
                logger.info(f"Added {len(new_photos)} new photos for VIN {vehicle_data.vin}")

            await db.execute(
                delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == existing_vehicle.id)
            )
            if vehicle_data.condition_assessments:
                for assessment in vehicle_data.condition_assessments:
                    db.add(
                        ConditionAssessmentModel(
                            type_of_damage=assessment.type_of_damage,
                            issue_description=assessment.issue_description,
                            car_id=existing_vehicle.id,
                        )
                    )
                    if assessment.issue_description in [
                        "Rejected Repair",
                        "Burn Engine",
                        "Mechanical",
                        "Replaced Vin",
                        "Burn",
                        "Undercarriage",
                        "Water/Flood",
                        "Burn Interior",
                        "Rollover",
                    ]:
                        existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        if not existing_vehicle.recommendation_status_reasons:
                            existing_vehicle.recommendation_status_reasons = f"{assessment.issue_description};"
                        else:
                            existing_vehicle.recommendation_status_reasons += f"i{assessment.issue_description};"

            if (
                vehicle_data.current_bid is not None
                and existing_vehicle.suggested_bid is not None
                and vehicle_data.current_bid > existing_vehicle.suggested_bid
            ):
                existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED

            if not existing_vehicle.sales_history:
                if vehicle_data.sales_history:
                    await save_sale_history(vehicle_data.sales_history, existing_vehicle.id, db)

            await db.commit()

            return False

        vehicle = CarModel(
            **vehicle_data.dict(exclude={"photos", "photos_hd", "sales_history", "condition_assessments"})
        )
        db.add(vehicle)
        await db.flush()
        if vehicle.fuel_type not in ["Gasoline", "Flexible Fuel", "Unknown"]:
            vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
            if not vehicle.recommendation_status_reasons:
                vehicle.recommendation_status_reasons = f"{vehicle.fuel_type};"
            else:
                vehicle.recommendation_status_reasons += f"{vehicle.fuel_type};"
        if vehicle.transmision != "Automatic":
            vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
            if not vehicle.recommendation_status_reasons:
                vehicle.recommendation_status_reasons = f"{vehicle.transmision};"
            else:
                vehicle.recommendation_status_reasons += f"{vehicle.transmision};"

        query = select(USZipModel).where(
            or_(USZipModel.copart_name == vehicle.location, USZipModel.iaai_name == vehicle.location)
        )
        result = await db.execute(query)
        locations = result.scalars().all()
        if not locations:
            await match_and_update_location(vehicle.location, vehicle.auction)

        if vehicle_data.condition_assessments:
            for assessment in vehicle_data.condition_assessments:
                if assessment.issue_description != "Unknown":
                    db.add(
                        ConditionAssessmentModel(
                            type_of_damage=assessment.type_of_damage,
                            issue_description=assessment.issue_description,
                            car_id=vehicle.id,
                        )
                    )
                    if assessment.issue_description in [
                        "Rejected Repair",
                        "Burn Engine",
                        "Mechanical",
                        "Replaced Vin",
                        "Burn",
                        "Undercarriage",
                        "Water/Flood",
                        "Burn Interior",
                        "Rollover",
                    ]:
                        vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        if not vehicle.recommendation_status_reasons:
                            vehicle.recommendation_status_reasons = f"{assessment.issue_description};"
                        else:
                            vehicle.recommendation_status_reasons += f"{assessment.issue_description};"

        if vehicle_data.photos:
            db.add_all([PhotoModel(url=p.url, car_id=vehicle.id, is_hd=False) for p in vehicle_data.photos])

        if vehicle_data.photos_hd:
            db.add_all([PhotoModel(url=p.url, car_id=vehicle.id, is_hd=True) for p in vehicle_data.photos_hd])

        if vehicle_data.sales_history:
            await save_sale_history(vehicle_data.sales_history, vehicle.id, db)

        logger.info(f"Vehicle {vehicle.vin} saved successfully with ID {vehicle.id}")
        await db.commit()
        return True

    except IntegrityError as e:
        if "unique constraint" in str(e).lower() and "vin" in str(e).lower():
            return False
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


async def get_vehicle_by_vin(db: AsyncSession, vin: str, current_user_id: int) -> Optional[CarModel]:
    """Get a vehicle by VIN from the database and mark if liked by current user."""
    liked_expr = case((user_likes.c.user_id == current_user_id, True), else_=False).label("liked")

    stmt = (
        select(CarModel, liked_expr)
        .outerjoin(user_likes, (CarModel.id == user_likes.c.car_id) & (user_likes.c.user_id == current_user_id))
        .options(selectinload(CarModel.photos), selectinload(CarModel.sales_history))
        .filter(CarModel.vin == vin)
    )

    result = await db.execute(stmt)
    row = result.first()

    if row:
        car, liked = row
        car.liked = bool(liked)
        return car

    return None


async def save_vehicle(db: AsyncSession, vehicle_data: CarCreateSchema) -> Optional[CarModel]:
    """Save a vehicle to the database if it doesn't already exist."""
    existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin, 1)
    if existing_vehicle:
        return None

    db_vehicle = CarModel(**vehicle_data.dict(exclude_unset=True))
    db.add(db_vehicle)
    return db_vehicle


async def get_filtered_vehicles(
    db: AsyncSession, filters: Dict[str, Any], ordering, page: int, page_size: int
) -> tuple[List[CarModel], int, int, Dict[str, Any]]:
    """Get filtered vehicles with pagination and liked status."""

    today = datetime.now(timezone.utc).date()
    today_naive = datetime.combine(today, time.min)
    user_id = filters["user_id"]

    liked_expr = case((user_likes.c.user_id == user_id, True), else_=False).label("liked")

    def apply_str_in_filter(field, values):
        return func.lower(field).in_([v.lower() for v in values if isinstance(v, str)])

    def apply_int_in_filter(field, values):
        return field.in_([int(v) for v in values if isinstance(v, int) or (isinstance(v, str) and v.isdigit())])

    base_query = (
        select(CarModel)
        .outerjoin(user_likes, (CarModel.id == user_likes.c.car_id) & (user_likes.c.user_id == user_id))
        .options(selectinload(CarModel.photos))
        .filter(
            CarModel.predicted_total_investments.isnot(None),
            CarModel.predicted_total_investments > 0,
            CarModel.suggested_bid.isnot(None),
            CarModel.suggested_bid > 0,
        )
    )

    # JOIN condition_assessments
    if filters.get("condition_assessments"):
        base_query = base_query.outerjoin(ConditionAssessmentModel, ConditionAssessmentModel.car_id == CarModel.id)
        issue_filters = ConditionAssessmentModel.issue_description.in_(filters["condition_assessments"])
        base_query = base_query.filter(issue_filters)

    # Location filter via zip_search
    if filters.get("zip_search"):
        logger.info(f"ZIP SEARCH DATA {filters.get("zip_search")}")

        zip_code, radius = filters["zip_search"]
        zip_row = await db.execute(select(USZipModel).where(USZipModel.zip == zip_code))
        zip_data = zip_row.scalar_one_or_none()

        if zip_data:
            logger.info(f"Find ZIP {zip_data.city}")
            lat1, lon1 = float(zip_data.lat), float(zip_data.lng)
            zip_rows = await db.execute(select(USZipModel))
            zips = zip_rows.scalars().all()

            nearby_locations = set()
            for z in zips:
                lat2, lon2 = float(z.lat), float(z.lng)
                dlat = radians(lat2 - lat1)
                dlon = radians(lon2 - lon1)
                a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
                c = 2 * asin(sqrt(a))
                dist = 6371 * c
                if dist <= radius:
                    if z.copart_name:
                        nearby_locations.add(z.copart_name.lower())
                    if z.iaai_name:
                        nearby_locations.add(z.iaai_name.lower())
            if nearby_locations:
                base_query = base_query.filter(apply_str_in_filter(CarModel.location, nearby_locations))
                logger.info(f"Nearby locations ----> {nearby_locations}")
            else:
                base_query = base_query.filter(False)
                logger.warning(f"No nearby auction locations found for ZIP={zip_code} within radius={radius}")
        else:
            raise HTTPException(status_code=404, detail=f"ZIP {zip_code} not found")

    # String filters
    for field_name, column in {
        "make": CarModel.make,
        "body_style": CarModel.body_style,
        "vehicle_type": CarModel.vehicle_type,
        "transmission": CarModel.transmision,
        "drive_type": CarModel.drive_type,
        "fuel_type": CarModel.fuel_type,
        "condition": CarModel.condition,
        "model": CarModel.model,
        "auction": CarModel.auction,
        "auction_name": CarModel.auction_name,
        "location": CarModel.location,
    }.items():
        if filters.get(field_name):
            base_query = base_query.filter(apply_str_in_filter(column, filters[field_name]))

    # Integer filters (list of values)
    if filters.get("engine_cylinder"):
        base_query = base_query.filter(apply_int_in_filter(CarModel.engine_cylinder, filters["engine_cylinder"]))

    # Numeric range filters
    if filters.get("mileage_min") is not None:
        base_query = base_query.filter(CarModel.mileage >= filters["mileage_min"])
    if filters.get("mileage_max") is not None:
        base_query = base_query.filter(CarModel.mileage <= filters["mileage_max"])

    if filters.get("predicted_profit_margin_min") is not None:
        base_query = base_query.filter(CarModel.predicted_profit_margin >= filters["predicted_profit_margin_min"])
    if filters.get("predicted_profit_margin_max") is not None:
        base_query = base_query.filter(CarModel.predicted_profit_margin <= filters["predicted_profit_margin_max"])

    if filters.get("predicted_roi_min") is not None:
        base_query = base_query.filter(CarModel.predicted_roi >= filters["predicted_roi_min"])
    if filters.get("predicted_roi_max") is not None:
        base_query = base_query.filter(CarModel.predicted_roi <= filters["predicted_roi_max"])

    if filters.get("min_owners_count") is not None:
        base_query = base_query.filter(CarModel.owners >= filters["min_owners_count"])
    if filters.get("max_owners_count") is not None:
        base_query = base_query.filter(CarModel.owners <= filters["max_owners_count"])

    if filters.get("min_accident_count") is not None:
        base_query = base_query.filter(CarModel.accident_count >= filters["min_accident_count"])
    if filters.get("max_accident_count") is not None:
        base_query = base_query.filter(CarModel.accident_count <= filters["max_accident_count"])

    if filters.get("min_year") is not None:
        base_query = base_query.filter(CarModel.year >= filters["min_year"])
    if filters.get("max_year") is not None:
        base_query = base_query.filter(CarModel.year <= filters["max_year"])

    # Date filters
    if filters.get("date_from"):
        date_from = filters["date_from"]
        if isinstance(date_from, str):
            date_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        base_query = base_query.filter(CarModel.date >= datetime.combine(date_from, time.min))
    else:
        base_query = base_query.filter(CarModel.date >= today_naive)

    if filters.get("date_to"):
        date_to = filters["date_to"]
        if isinstance(date_to, str):
            date_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        base_query = base_query.filter(CarModel.date <= datetime.combine(date_to, time.max))

    if filters.get("recommended_only"):
        base_query = base_query.filter(CarModel.recommendation_status == RecommendationStatus.RECOMMENDED)

    if filters.get("liked"):
        if user_id is not None:
            base_query = base_query.filter(user_likes.c.user_id == user_id)
        else:
            raise ValueError("user_id is required when filtering by liked=True")

    # Count
    count_query = select(func.count()).select_from(base_query.subquery())
    total_count = await db.scalar(count_query)
    total_pages = (total_count + page_size - 1) // page_size

    # Aggregation
    bids_info = {}
    if page == 1:
        stats_query = base_query.with_only_columns(
            func.min(CarModel.current_bid),
            func.max(CarModel.current_bid),
            func.avg(CarModel.current_bid),
        )
        result = await db.execute(stats_query)
        min_bid, max_bid, avg_bid = result.fetchone() or (0, 0, 0.0)
        bids_info = {
            "min_bid": min_bid,
            "max_bid": max_bid,
            "avg_bid": round(avg_bid or 0.0, 2),
            "total_count": total_count,
        }

    # Final query with liked column and ordering
    full_query = base_query.add_columns(liked_expr)
    order_clause = ORDERING_MAP.get(ordering, desc(CarModel.created_at))
    full_query = full_query.order_by(order_clause)

    result = await db.execute(full_query.offset((page - 1) * page_size).limit(page_size))
    rows = result.all()

    vehicles_with_liked = []
    for car, liked in rows:
        car.liked = bool(liked)
        vehicles_with_liked.append(car)

    return vehicles_with_liked, total_count, total_pages, bids_info


async def get_bidding_hub_vehicles(
    db: AsyncSession,
    page: int,
    page_size: int,
    current_user: UserModel,
    sort_by: str = "date",
    sort_order: str = "desc",
) -> tuple[List[CarModel], int, int]:
    """Get vehicles in the bidding hub with pagination and sorting, including the last user who made a manipulation."""
    async with db as session:

        order_func = asc if sort_order.lower() == "asc" else desc

        history_alias = aliased(HistoryModel)
        subquery = select(
            history_alias.id.label("id"),
            history_alias.car_id.label("car_id"),
            history_alias.user_id.label("user_id"),
            over(
                func.row_number(),
                partition_by=history_alias.car_id,
                order_by=history_alias.created_at.desc(),
            ).label("rn"),
        ).subquery()

        query = (
            select(CarModel)
            .outerjoin(subquery, (subquery.c.car_id == CarModel.id) & (subquery.c.rn == 1))
            .options(selectinload(CarModel.bidding_hub_history).selectinload(HistoryModel.user))
        )

        if current_user.has_role(UserRoleEnum.ADMIN):
            query = query.filter(
                ~CarModel.car_status.in_(
                    [
                        CarStatus.NEW,
                    ]
                )
            )
        else:
            query = query.filter(
                ~CarModel.car_status.in_(
                    [
                        CarStatus.NEW,
                        CarStatus.DELETED_FROM_BIDDING_HUB,
                    ]
                )
            )

        if sort_by == "user":
            query = query.join(UserModel, UserModel.id == subquery.c.user_id)
            query = query.order_by(order_func(UserModel.email))
        else:
            sort_field_mapping = {
                "vehicle": CarModel.vehicle,
                "auction": CarModel.auction,
                "location": CarModel.location,
                "date": CarModel.date,
                "lot": CarModel.lot,
                "avg_market_price": CarModel.avg_market_price,
                "predicted_total_investments": CarModel.predicted_total_investments,
                "predicted_profit_margin": CarModel.predicted_profit_margin,
                "predicted_roi": CarModel.predicted_roi,
                "actual_bid": CarModel.actual_bid,
                "status": CarModel.car_status,
                "current_bid": CarModel.current_bid,
                "suggested_bid": CarModel.suggested_bid,
            }
            sort_field = sort_field_mapping.get(sort_by)

            if sort_field:
                query = query.order_by(order_func(sort_field))
            else:
                raise HTTPException(status_code=400, detail=f"Sorting by {sort_by} not alowed")

        total_count = await session.scalar(select(func.count()).select_from(query.subquery()))
        total_pages = (total_count + page_size - 1) // page_size

        result = await session.execute(query.offset((page - 1) * page_size).limit(page_size))
        vehicles = result.scalars().all()

        return vehicles, total_count, total_pages


async def get_vehicle_by_id(db: AsyncSession, car_id: int, user_id: Optional[int] = None) -> Optional[CarModel]:
    """Get a vehicle by ID with related data and liked status."""

    # Fetch the car and related data
    result = await db.execute(
        select(CarModel)
        .options(
            selectinload(CarModel.photos_hd),
            selectinload(CarModel.condition_assessments),
            selectinload(CarModel.sales_history),
        )
        .filter(CarModel.id == car_id)
    )
    car = result.scalars().first()
    if not car:
        return None

    # Check if the car is liked by the user
    if user_id is not None:
        liked_result = await db.execute(
            select(user_likes.c.user_id).filter(user_likes.c.user_id == user_id, user_likes.c.car_id == car_id)
        )
        car.liked = liked_result.first() is not None
    else:
        car.liked = False

    return car


async def update_vehicle_status(db: AsyncSession, car_id: int, car_status: str) -> Optional[CarModel]:
    """Update the status of a vehicle."""
    result = await db.execute(select(CarModel).where(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    old_status = car.car_status.value

    car.car_status = car_status

    if car.car_status == CarStatus.WON:
        if not car.actual_bid:
            raise HTTPException(status_code=400, detail="First fill out the actual bid")
        result = await db.execute(select(HistoryModel).where(HistoryModel.car_id == car_id))
        car_inventory_model = CarInventoryModel(
            car=car,
            vehicle=car.vehicle,
            vin=car.vin,
            vehicle_cost=car.actual_bid,
            car_status=CarInventoryStatus.AWAITING_DELIVERY,
        )
        db.add(car_inventory_model)
        await db.commit()
        await db.refresh(car_inventory_model)
        for history in result.scalars().all():
            history.car_inventory_id = car_inventory_model.id
            db.add(history)

    await db.commit()
    await db.refresh(car)
    return car, old_status


async def get_parts_by_vehicle_id(db: AsyncSession, car_id: int) -> List[PartModel]:
    """Get parts for a vehicle by its ID."""
    result = await db.execute(select(PartModel).filter(PartModel.car_id == car_id))
    return result.scalars().all()


async def add_part_to_vehicle(
    db: AsyncSession, car_id: int, part_data: Dict[str, Any]
) -> Optional[tuple[PartModel, CarModel]]:
    """Add a part to a vehicle."""
    logger.info(f"Adding part to vehicle. car_id: {car_id}, part_data: {part_data}")
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        logger.error(f"Vehicle not found for car_id: {car_id}")
        return None

    new_part = PartModel(**part_data, car_id=car_id)
    logger.info(f"Created new part: {new_part.__dict__}")
    db.add(new_part)

    if car.parts_cost is None or car.parts_cost <= 0:
        car.parts_cost = new_part.value
        logger.info(f"Updated car.parts_cost to {new_part.value} as it was None or <= 0")
    else:
        car.parts_cost += new_part.value
        logger.info(f"Incremented car.parts_cost by {new_part.value}, new value: {car.parts_cost}")

    if car.suggested_bid is not None:
        car.suggested_bid = car.predicted_total_investments - car.sum_of_investments
        logger.info(
            f"Updated suggested_bid to {car.suggested_bid} based on predicted_total_investments: {car.predicted_total_investments}, parts_cost: {car.parts_cost}, auction_fee: {car.auction_fee}"
        )

    if car.current_bid and car.current_bid > car.suggested_bid:
        car.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
        logger.info(
            f"Set recommendation_status to NOT_RECOMMENDED as current_bid: {car.current_bid} > suggested_bid: {car.suggested_bid}"
        )

    db.add(car)
    await db.commit()
    await db.refresh(new_part)
    await db.refresh(car)
    logger.info(f"Part added successfully. Part ID: {new_part.id}, Updated Car: {car.__dict__}")
    return new_part, car


async def update_part(
    db: AsyncSession, car_id: int, part_id: int, part_data: Dict[str, Any]
) -> Optional[tuple[PartModel, CarModel]]:
    """Update a part for a vehicle."""
    logger.info(f"Updating part. car_id: {car_id}, part_id: {part_id}, part_data: {part_data}")
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        logger.error(f"Vehicle not found for car_id: {car_id}")
        return None
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    existing_part = result.scalars().first()
    if not existing_part:
        logger.error(f"Part not found for part_id: {part_id}, car_id: {car_id}")
        return None

    temp_value = existing_part.value
    logger.info(f"Original part value: {temp_value}")

    for key, value in part_data.items():
        setattr(existing_part, key, value)
        logger.info(f"Updated part.{key} to {value}")

    if existing_part.value != temp_value:
        car.parts_cost += existing_part.value - temp_value
        logger.info(f"Adjusted car.parts_cost by {existing_part.value - temp_value}, new value: {car.parts_cost}")

    if car.suggested_bid is not None:
        car.suggested_bid = car.predicted_total_investments - car.sum_of_investments
        logger.info(
            f"Updated suggested_bid to {car.suggested_bid} based on predicted_total_investments: {car.predicted_total_investments}, parts_cost: {car.parts_cost}, auction_fee: {car.auction_fee}"
        )

    if car.current_bid and car.current_bid > car.suggested_bid:
        car.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
        logger.info(
            f"Set recommendation_status to NOT_RECOMMENDED as current_bid: {car.current_bid} > suggested_bid: {car.suggested_bid}"
        )

    db.add(car)
    db.add(existing_part)
    await db.commit()
    await db.refresh(existing_part)
    await db.refresh(car)
    logger.info(f"Part updated successfully. Part ID: {existing_part.id}, Updated Car: {car.__dict__}")
    return existing_part, car


async def delete_part(db: AsyncSession, car_id: int, part_id: int) -> tuple[bool, CarModel]:
    """Delete a part for a vehicle."""
    logger.info(f"Deleting part. car_id: {car_id}, part_id: {part_id}")
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        logger.error(f"Vehicle not found for car_id: {car_id}")
        return False, None
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    part = result.scalars().first()
    if not part:
        logger.error(f"Part not found for part_id: {part_id}, car_id: {car_id}")
        return False, car

    logger.info(f"Part to delete: {part.__dict__}, value: {part.value}")
    car.parts_cost -= part.value
    logger.info(f"Decremented car.parts_cost by {part.value}, new value: {car.parts_cost}")

    if car.suggested_bid is not None:
        car.suggested_bid = car.predicted_total_investments - car.sum_of_investments
        logger.info(
            f"Updated suggested_bid to {car.suggested_bid} based on predicted_total_investments: {car.predicted_total_investments}, parts_cost: {car.parts_cost}, auction_fee: {car.auction_fee}"
        )

    if car.current_bid and car.current_bid > car.suggested_bid:
        car.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
        logger.info(
            f"Set recommendation_status to NOT_RECOMMENDED as current_bid: {car.current_bid} > suggested_bid: {car.suggested_bid}"
        )

    db.add(car)
    await db.delete(part)
    await db.commit()
    await db.refresh(car)
    logger.info(f"Part deleted successfully. Updated Car: {car.__dict__}")
    return True, car


async def bulk_save_vehicles(db: AsyncSession, vehicles: List[CarCreateSchema]) -> List[str]:
    """Bulk save vehicles and return skipped VINs."""
    skipped_vins = []
    for vehicle_data in vehicles:
        success = await save_vehicle_with_photos(vehicle_data, db)
        await db.commit()
        if not success:
            skipped_vins.append(vehicle_data.vin)
    return skipped_vins
