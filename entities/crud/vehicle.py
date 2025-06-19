from datetime import datetime, time, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, asc, desc, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
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
        logger.debug(f"More than 3 sales history records provided for car ID {car_id}. Car will not be recomendet for purchase.")
        car = get_vehicle_by_id(db, car_id)
        car.recomendation_status = RecommendationStatus.NOT_RECOMMENDED
        db.add(car)
        await db.commit()
    for history_data in sale_history_data:
        sales_history = CarSaleHistoryModel(**history_data.dict(), car_id=car_id)
        if not sales_history.source:
            sales_history.source = "Unknown"
        db.add(sales_history)
        await db.commit()


async def save_vehicle_with_photos(vehicle_data: CarCreateSchema, db: AsyncSession) -> bool:
    """Save a single vehicle and its photos. Update all fields and photos if vehicle already exists."""
    try:
        existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin)
        if existing_vehicle:
            logger.info(f"Vehicle with VIN {vehicle_data.vin} already exists. Updating data...")

            for field, value in vehicle_data.dict(exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}).items():
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

            await db.execute(delete(ConditionAssessmentModel).where(ConditionAssessmentModel.car_id == existing_vehicle.id))
            if vehicle_data.condition_assessments:
                for assessment in vehicle_data.condition_assessments:
                    db.add(ConditionAssessmentModel(
                        type_of_damage=assessment.type_of_damage,
                        issue_description=assessment.issue_description,
                        car_id=existing_vehicle.id
                    ))

            await db.execute(delete(CarSaleHistoryModel).where(CarSaleHistoryModel.car_id == existing_vehicle.id))
            if vehicle_data.sales_history:
                await save_sale_history(vehicle_data.sales_history, existing_vehicle.id, db)

            return False

        vehicle = CarModel(
            **vehicle_data.dict(exclude={"photos", "photos_hd", "sales_history", "condition_assessments"})
        )
        db.add(vehicle)
        await db.flush()

        if vehicle_data.condition_assessments:
            for assessment in vehicle_data.condition_assessments:
                db.add(ConditionAssessmentModel(
                    type_of_damage=assessment.type_of_damage,
                    issue_description=assessment.issue_description,
                    car_id=vehicle.id
                ))

        if vehicle_data.photos:
            db.add_all([PhotoModel(url=p.url, car_id=vehicle.id, is_hd=False) for p in vehicle_data.photos])

        if vehicle_data.photos_hd:
            db.add_all([PhotoModel(url=p.url, car_id=vehicle.id, is_hd=True) for p in vehicle_data.photos_hd])

        if vehicle_data.sales_history:
            await save_sale_history(vehicle_data.sales_history, vehicle.id, db)

        logger.info(f"Vehicle {vehicle.vin} saved successfully with ID {vehicle.id}")
        return True

    except IntegrityError as e:
        if "unique constraint" in str(e).lower() and "vin" in str(e).lower():
            return False
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")



async def get_vehicle_by_vin(db: AsyncSession, vin: str) -> Optional[CarModel]:
    """Get a vehicle by VIN from the database."""
    result = await db.execute(select(CarModel).options(selectinload(CarModel.photos)).filter(CarModel.vin == vin))
    return result.scalars().first()


async def save_vehicle(db: AsyncSession, vehicle_data: CarCreateSchema) -> Optional[CarModel]:
    """Save a vehicle to the database if it doesn't already exist."""
    existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin)
    if existing_vehicle:
        return None

    db_vehicle = CarModel(**vehicle_data.dict(exclude_unset=True))
    db.add(db_vehicle)
    return db_vehicle


async def get_filtered_vehicles(
    db: AsyncSession, filters: Dict[str, Any], page: int, page_size: int
) -> tuple[List[CarModel], int, int]:
    """Get filtered vehicles with pagination."""

    today = datetime.now(timezone.utc).date()
    today_naive = datetime.combine(today, time.min)

    query = (
        select(CarModel)
        .options(selectinload(CarModel.photos))
        .filter(CarModel.date >= today_naive)
    )

    if "make" in filters and filters["make"]:
        query = query.filter(CarModel.make.in_(filters["make"]))
    if "model" in filters and filters["model"]:
        query = query.filter(CarModel.model.in_(filters["model"]))
    if "auction" in filters and filters["auction"]:
        query = query.filter(CarModel.auction.in_(filters["auction"]))
    if "auction_name" in filters and filters["auction_name"]:
        query = query.filter(CarModel.auction_name.in_(filters["auction_name"]))
    if "location" in filters and filters["location"]:
        query = query.filter(CarModel.location.in_(filters["location"]))
    if "mileage_min" in filters and filters["mileage_min"] is not None:
        query = query.filter(CarModel.mileage >= filters["mileage_min"])
    if "mileage_max" in filters and filters["mileage_max"] is not None:
        query = query.filter(CarModel.mileage <= filters["mileage_max"])
    if "min_accident_count" in filters and filters["min_accident_count"] is not None:
        query = query.filter(CarModel.accident_count >= filters["min_accident_count"])
    if "max_accident_count" in filters and filters["max_accident_count"] is not None:
        query = query.filter(CarModel.accident_count <= filters["max_accident_count"])
    if "min_year" in filters and filters["min_year"] is not None:
        query = query.filter(CarModel.year >= filters["min_year"])
    if "max_year" in filters and filters["max_year"] is not None:
        query = query.filter(CarModel.year <= filters["max_year"])
    if "liked" in filters and filters["liked"]:
        user_id = filters.get("user_id")
        if user_id is not None:
            query = query.join(user_likes, CarModel.id == user_likes.c.car_id)
            query = query.filter(user_likes.c.user_id == user_id)
        else:
            raise ValueError("user_id is required when filtering by liked=True")

    total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
    total_pages = (total_count + page_size - 1) // page_size

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    vehicles = result.scalars().all()

    return vehicles, total_count, total_pages


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

        last_history_subquery = (
            select(HistoryModel)
            .where(HistoryModel.car_id == CarModel.id)
            .order_by(HistoryModel.created_at.desc())
            .limit(1)
            .subquery()
        )

        query = select(CarModel).options(selectinload(CarModel.bidding_hub_history).selectinload(HistoryModel.user))

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
            query = query.join(last_history_subquery, last_history_subquery.c.car_id == CarModel.id).join(
                UserModel, UserModel.id == last_history_subquery.c.user_id
            )
            query = query.order_by(order_func(UserModel.email))
        else:
            sort_field_mapping = {
                "vehicle": CarModel.vehicle,
                "auction": CarModel.auction,
                "location": CarModel.location,
                "date": CarModel.date,
                "lot": CarModel.lot,
                "avg_market_price": CarModel.avg_market_price,
                "status": CarModel.car_status,
            }
            sort_field = sort_field_mapping.get(sort_by)
            if sort_field:
                query = query.order_by(order_func(sort_field))

        total_count = await session.scalar(select(func.count()).select_from(query.subquery()))
        total_pages = (total_count + page_size - 1) // page_size

        result = await session.execute(query.offset((page - 1) * page_size).limit(page_size))
        vehicles = result.scalars().all()

        return vehicles, total_count, total_pages


async def get_vehicle_by_id(db: AsyncSession, car_id: int) -> Optional[CarModel]:
    """Get a vehicle by ID with related data."""
    result = await db.execute(
        select(CarModel)
        .options(
            selectinload(CarModel.photos_hd),
            selectinload(CarModel.condition_assessments),
            selectinload(CarModel.sales_history),
        )
        .filter(CarModel.id == car_id)
    )
    return result.scalars().first()


async def update_vehicle_status(db: AsyncSession, car_id: int, car_status: str) -> Optional[CarModel]:
    """Update the status of a vehicle."""
    result = await db.execute(select(CarModel).where(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    car.car_status = car_status

    if car.car_status == CarStatus.WON:
        result = await db.execute(select(HistoryModel).where(HistoryModel.car_id == car_id))
        car_inventory_model = CarInventoryModel(
            car=car,
            vehicle=car.vehicle,
            vin=car.vin,
            vehicle_cost=car.suggested_bid,
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
    return car


async def get_parts_by_vehicle_id(db: AsyncSession, car_id: int) -> List[PartModel]:
    """Get parts for a vehicle by its ID."""
    result = await db.execute(select(PartModel).filter(PartModel.car_id == car_id))
    return result.scalars().all()


async def add_part_to_vehicle(db: AsyncSession, car_id: int, part_data: Dict[str, Any]) -> Optional[PartModel]:
    """Add a part to a vehicle."""
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        return None

    new_part = PartModel(**part_data, car_id=car_id)
    db.add(new_part)
    if car.parts_cost is None:
        car.parts_cost = new_part.value
    else:
        car.parts_cost += new_part.value
    if car.total_investment and car:
        car.suggested_bid = car.total_investment - car.parts_cost
    await db.commit()
    await db.refresh(new_part)
    return new_part


async def update_part(db: AsyncSession, car_id: int, part_id: int, part_data: Dict[str, Any]) -> Optional[PartModel]:
    """Update a part for a vehicle."""
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        return None
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    existing_part = result.scalars().first()
    if not existing_part:
        return None
    temp_value = existing_part.value

    for key, value in part_data.items():
        setattr(existing_part, key, value)

    if existing_part.value != temp_value:
        car.parts_cost += existing_part.value - temp_value
        if car.total_investment and car:
            car.suggested_bid = car.total_investment - car.parts_cost

    await db.commit()
    await db.refresh(existing_part)
    return existing_part


async def delete_part(db: AsyncSession, car_id: int, part_id: int) -> bool:
    """Delete a part for a vehicle."""
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        return None
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    part = result.scalars().first()
    if not part:
        return False
    car.parts_cost -= part.value
    if car.total_investment and car:
        car.suggested_bid = car.total_investment - car.parts_cost

    await db.delete(part)
    await db.commit()
    return True


async def bulk_save_vehicles(db: AsyncSession, vehicles: List[CarCreateSchema]) -> List[str]:
    """Bulk save vehicles and return skipped VINs."""
    skipped_vins = []
    for vehicle_data in vehicles:
        success = await save_vehicle_with_photos(vehicle_data, db)
        await db.commit()
        if not success:
            skipped_vins.append(vehicle_data.vin)
    return skipped_vins
