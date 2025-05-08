from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, asc, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from models.vehicle import (
    CarModel,
    PhotoModel,
    CarSaleHistoryModel,
    PartModel,
    CarStatus,
    BiddingHubHistoryModel,
    ConditionAssessmentModel,
)
from models.user import UserModel, UserRoleEnum
from schemas.vehicle import CarCreateSchema
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def save_sale_history(sale_history_data: List[CarCreateSchema], car_id: int, db: AsyncSession) -> None:
    """Save sales history for a vehicle."""
    for history_data in sale_history_data:
        sales_history = CarSaleHistoryModel(**history_data.dict(), car_id=car_id)
        db.add(sales_history)


async def save_vehicle_with_photos(vehicle_data: CarCreateSchema, db: AsyncSession) -> bool:
    """Save a single vehicle and its photos."""
    try:
        vehicle = CarModel(
            **vehicle_data.dict(exclude={"photos", "photos_hd", "sales_history", "condition_assessments"})
        )
        db.add(vehicle)
        await db.flush()
        if hasattr(vehicle_data, "condition_assessments") and vehicle_data.condition_assessments:
            for condition_assessment_data in vehicle_data.condition_assessments:
                logger.info(f"Condition assessment data: {condition_assessment_data}")
                condition_assessment = ConditionAssessmentModel(
                    type_of_damage=condition_assessment_data.type_of_damage,
                    issue_description=condition_assessment_data.issue_description,
                    car_id=vehicle.id,
                )
                db.add(condition_assessment)

        if hasattr(vehicle_data, "photos") and vehicle_data.photos:
            for photo_data in vehicle_data.photos:
                photo = PhotoModel(url=photo_data.url, car_id=vehicle.id, is_hd=False)
                db.add(photo)

        if hasattr(vehicle_data, "photos_hd") and vehicle_data.photos_hd:
            for photo_data_hd in vehicle_data.photos_hd:
                photo_hd = PhotoModel(url=photo_data_hd.url, car_id=vehicle.id, is_hd=True)
                db.add(photo_hd)

        logger.info(f"Vehicle {vehicle.vin} saved successfully with ID {vehicle.id}.")

        if hasattr(vehicle_data, "sales_history") and vehicle_data.sales_history:
            await save_sale_history(vehicle_data.sales_history, vehicle.id, db)

        return True

    except IntegrityError as e:
        if "unique constraint" in str(e).lower() and "vin" in str(e).lower():
            return False
        else:
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
    query = select(CarModel).options(selectinload(CarModel.photos))

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

    order_func = asc if sort_order.lower() == "asc" else desc

    last_history_subquery = (
        select(BiddingHubHistoryModel)
        .where(BiddingHubHistoryModel.car_id == CarModel.id)
        .order_by(BiddingHubHistoryModel.created_at.desc())
        .limit(1)
        .subquery()
    )

    query = select(CarModel).options(
        selectinload(CarModel.bidding_hub_history).selectinload(BiddingHubHistoryModel.user)
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

    total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
    total_pages = (total_count + page_size - 1) // page_size

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
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

    await db.commit()
    await db.refresh(car)
    return car


async def add_part_to_vehicle(db: AsyncSession, car_id: int, part_data: Dict[str, Any]) -> Optional[PartModel]:
    """Add a part to a vehicle."""
    result = await db.execute(select(CarModel).filter(CarModel.id == car_id))
    car = result.scalars().first()
    if not car:
        return None

    new_part = PartModel(**part_data, car_id=car_id)
    db.add(new_part)
    await db.commit()
    await db.refresh(new_part)
    return new_part


async def update_part(db: AsyncSession, car_id: int, part_id: int, part_data: Dict[str, Any]) -> Optional[PartModel]:
    """Update a part for a vehicle."""
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    existing_part = result.scalars().first()
    if not existing_part:
        return None

    for key, value in part_data.items():
        setattr(existing_part, key, value)

    await db.commit()
    await db.refresh(existing_part)
    return existing_part


async def delete_part(db: AsyncSession, car_id: int, part_id: int) -> bool:
    """Delete a part for a vehicle."""
    result = await db.execute(select(PartModel).filter(PartModel.id == part_id, PartModel.car_id == car_id))
    part = result.scalars().first()
    if not part:
        return False

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
