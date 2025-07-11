from datetime import datetime, time, timezone
from typing import List, Dict, Any, Optional
from ordering_constr import ORDERING_MAP
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, asc, desc, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy import case, literal_column
from sqlalchemy.sql import over
from models.vehicle import (
    CarModel,
    PhotoModel,
    CarSaleHistoryModel,
    PartModel,
    CarStatus,
    HistoryModel,
    ConditionAssessmentModel,
    CarInventoryStatus,
    CarInventoryModel,
    FeeModel,
    RecommendationStatus,
)
from models.user import UserModel, UserRoleEnum, user_likes
from schemas.vehicle import CarCreateSchema
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def save_sale_history(sale_history_data: List[CarCreateSchema], car_id: int, db: AsyncSession) -> None:
    """Save sales history for a vehicle."""
    if len(sale_history_data) >= 3:
        logger.debug(
            f"More than 3 sales history records provided for car ID {car_id}. Car will not be recomendet for purchase."
        )
        car = await get_vehicle_by_id(db, car_id)
        car.recomendation_status = RecommendationStatus.NOT_RECOMMENDED
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

        if vehicle_data.condition_assessments:
            for assessment in vehicle_data.condition_assessments:
                db.add(
                    ConditionAssessmentModel(
                        type_of_damage=assessment.type_of_damage,
                        issue_description=assessment.issue_description,
                        car_id=vehicle.id,
                    )
                )

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
) -> tuple[List[CarModel], int, int]:
    """Get filtered vehicles with pagination and liked status."""

    today = datetime.now(timezone.utc).date()
    today_naive = datetime.combine(today, time.min)
    user_id = filters["user_id"]

    liked_expr = case((user_likes.c.user_id == user_id, True), else_=False).label("liked")

    def apply_in_filter(field, values):
        return func.lower(field).in_([v.lower() for v in values])

    # Базовий запит без liked_expr для subquery
    base_query = (
        select(CarModel)
        .outerjoin(user_likes, (CarModel.id == user_likes.c.car_id) & (user_likes.c.user_id == user_id))
        .options(selectinload(CarModel.photos))
        .filter(
            CarModel.date >= today_naive,
            CarModel.predicted_total_investments.isnot(None),
            CarModel.predicted_total_investments > 0,
            CarModel.suggested_bid.isnot(None),
            CarModel.suggested_bid > 0,
        )
    )

    # Застосування фільтрів
    if filters.get("make"):
        base_query = base_query.filter(apply_in_filter(CarModel.make, filters["make"]))
    if filters.get("body_style"):
        base_query = base_query.filter(apply_in_filter(CarModel.body_style, filters["body_style"]))
    if filters.get("vehicle_type"):
        base_query = base_query.filter(apply_in_filter(CarModel.vehicle_type, filters["vehicle_type"]))
    if filters.get("transmission"):
        base_query = base_query.filter(apply_in_filter(CarModel.transmision, filters["transmission"]))
    if filters.get("drive_type"):
        base_query = base_query.filter(apply_in_filter(CarModel.drive_type, filters["drive_type"]))
    if filters.get("engine_cylinder"):
        base_query = base_query.filter(apply_in_filter(CarModel.engine_cylinder, filters["engine_cylinder"]))
    if filters.get("fuel_type"):
        base_query = base_query.filter(apply_in_filter(CarModel.fuel_type, filters["fuel_type"]))
    if filters.get("condition"):
        base_query = base_query.filter(apply_in_filter(CarModel.condition, filters["condition"]))
    if filters.get("model"):
        base_query = base_query.filter(apply_in_filter(CarModel.model, filters["model"]))
    if filters.get("auction"):
        base_query = base_query.filter(apply_in_filter(CarModel.auction, filters["auction"]))
    if filters.get("auction_name"):
        base_query = base_query.filter(apply_in_filter(CarModel.auction_name, filters["auction_name"]))
    if filters.get("location"):
        base_query = base_query.filter(apply_in_filter(CarModel.location, filters["location"]))

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

    if filters.get("date_from"):
        date_from = datetime.strptime(filters["date_from"], "%Y-%m-%d").date()
        base_query = base_query.filter(CarModel.date >= date_from)
    if filters.get("date_to"):
        date_to = datetime.strptime(filters["date_to"], "%Y-%m-%d").date()
        base_query = base_query.filter(CarModel.date <= date_to)

    if filters.get("liked"):
        if user_id is not None:
            base_query = base_query.filter(user_likes.c.user_id == user_id)
        else:
            raise ValueError("user_id is required when filtering by liked=True")

    # Підрахунок кількості записів
    count_query = select(func.count()).select_from(base_query.subquery())
    total_count = await db.scalar(count_query)
    total_pages = (total_count + page_size - 1) // page_size

    # Агрегація current_bid
    bids_info = {}

    if page == 1:
        stats_query = select(
            func.min(CarModel.current_bid),
            func.max(CarModel.current_bid),
            func.avg(CarModel.current_bid)
        ).select_from(base_query.subquery())

        result = await db.execute(stats_query)
        min_bid, max_bid, avg_bid = result.fetchone() or (0, 0, 0.0)

        bids_info = {
            "min_bid": min_bid,
            "max_bid": max_bid,
            "avg_bid": avg_bid,
            "total_count": total_count,
        }

    # Основний запит з liked_expr та сортуванням
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
        car.suggested_bid = car.predicted_total_investments - car.parts_cost - (car.auction_fee or 0)
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
        car.suggested_bid = car.predicted_total_investments - car.parts_cost - (car.auction_fee or 0)
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
        car.suggested_bid = car.predicted_total_investments - car.parts_cost - (car.auction_fee or 0)
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
