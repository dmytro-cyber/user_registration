import logging
from datetime import datetime, time, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, List, Optional, Tuple, Iterable

from fastapi import HTTPException
from sqlalchemy import and_, asc, case, delete, desc, func, literal_column, or_, select, bindparam, update, exists
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload, with_loader_criteria
from sqlalchemy.sql import over

from core.setup import match_and_update_location
from models.user import UserModel, UserRoleEnum, user_likes
from models.admin import FilterModel
from models.vehicle import (
    AutoCheckModel,
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
    RelevanceStatus,
    USZipModel,
)
from ordering_constr import ORDERING_MAP
from schemas.vehicle import CarBulkCreateSchema, CarCreateSchema
from core.dependencies import get_s3_storage_client

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

SITE_MAP = {
    1: "Copart",
    2: "IAAI",
}

async def update_cars_relevance(payload: Dict, db: AsyncSession) -> None:
    s3_client = get_s3_storage_client()
    lots_by_site = {}

    for item in payload["data"]:
        site_str = SITE_MAP.get(item["site"])
        if site_str:
            lots_by_site.setdefault(site_str, set()).add(item["lot_id"])

    if not lots_by_site:
        return

    filter_condition = or_(*[
        and_(func.lower(CarModel.auction) == site.lower(), CarModel.lot.in_(lot_ids))
        for site, lot_ids in lots_by_site.items()
    ])

    # 1. Delete IRRELEVANT cars directly
    await db.execute(
        delete(CarModel).where(
            and_(
                or_(
                    CarModel.relevance == RelevanceStatus.IRRELEVANT,
                    CarModel.relevance.is_(None),
                ),
            filter_condition)
        )
    )

    # 2. Get ACTIVE car ids to archive
    stmt_active_ids = select(CarModel.id).where(
        and_(CarModel.relevance == RelevanceStatus.ACTIVE, filter_condition)
    )
    result = await db.execute(stmt_active_ids)
    to_archive_ids = [row for row in result.scalars()]

    # 3. Delete screenshots from S3
    if to_archive_ids:
        stmt_checks = select(AutoCheckModel.screenshot_url).where(
            AutoCheckModel.car_id.in_(to_archive_ids)
        )
        result_checks = await db.execute(stmt_checks)
        for (screenshot_url,) in result_checks.all():
            if screenshot_url:
                file_name = screenshot_url.split("/")[-1]
                try:
                    s3_client.delete_file(file_name)
                except Exception as e:
                    print(f"Failed to delete file {file_name} from S3: {e}")

        # 4. Update relevance to ARCHIVAL
        await db.execute(
            update(CarModel)
            .where(CarModel.id.in_(to_archive_ids))
            .values(relevance=RelevanceStatus.ARCHIVAL)
        )

    await db.commit()


async def save_vehicle_with_photos(vehicle_data: CarCreateSchema, ivent: str, db: AsyncSession) -> bool:
    """Save a single vehicle and its photos. Update all fields and photos if vehicle already exists."""
    try:
        to_parse = False
        existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin, 1)
        if existing_vehicle:
            # if existing_vehicle.relevance == RelevanceStatus.ACTIVE:
            #     pass
            # elif existing_vehicle.relevance == RelevanceStatus.ARCHIVAL and ivent == "update":
            #     query = select(FilterModel).where(
            #         FilterModel.make == vehicle_data.make,
            #         FilterModel.model == vehicle_data.model,
            #         FilterModel.year_from <= vehicle_data.year,
            #         FilterModel.year_to >= vehicle_data.year,
            #         FilterModel.odometer_max >= vehicle_data.mileage
            #     )
            #     filter_ex = await db.execute(query)
            #     filter_res = filter_ex.scalars().one_or_none()
            #     if filter_res:
            #         existing_vehicle.relevance = RelevanceStatus.ACTIVE
            #         to_parse = True
            #     else:
            #         existing_vehicle.relevance = RelevanceStatus.IRRELEVANT
            existing_vehicle.relevance = RelevanceStatus.IRRELEVANT

            
            # logger.info(f"Vehicle with VIN {vehicle_data.vin} already exists. Updating data...")

            for field, value in vehicle_data.dict(
                exclude={"photos", "photos_hd", "sales_history", "condition_assessments"}
            ).items():
                if (value is not None or field == "date"):
                    setattr(existing_vehicle, field, value)
                    if field == "fuel_type" and value not in ["Gasoline", "Flexible Fuel", "Unknown"]:
                        existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        if not existing_vehicle.recommendation_status_reasons:
                            existing_vehicle.recommendation_status_reasons = f"{value};"
                        else:
                            if f"{value};" not in existing_vehicle.recommendation_status_reasons:
                                existing_vehicle.recommendation_status_reasons += f"{value};"
                    if field == "transmision" and value != "Automatic":
                        existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
                        if not existing_vehicle.recommendation_status_reasons:
                            existing_vehicle.recommendation_status_reasons = f"{value};"
                        else:
                            if f"{value};" not in existing_vehicle.recommendation_status_reasons:
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
                # logger.info(f"Added {len(new_photos)} new photos for VIN {vehicle_data.vin}")

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
                            if f"{assessment.issue_description};" not in existing_vehicle.recommendation_status_reasons:
                                existing_vehicle.recommendation_status_reasons += f"{assessment.issue_description};"

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

            return to_parse

        else:
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

            # query = select(FilterModel).where(
            #     FilterModel.make == vehicle_data.make,
            #     or_(
            #         FilterModel.model == vehicle_data.model,
            #         FilterModel.model.is_(None)
            #     ),
            #     FilterModel.year_from <= vehicle_data.year,
            #     FilterModel.year_to >= vehicle_data.year,
            #     FilterModel.odometer_max >= vehicle_data.mileage
            # )
            # filter_ex = await db.execute(query)
            # filter_res = filter_ex.scalars().one_or_none()
            # if filter_res:
            #     vehicle.relevance = RelevanceStatus.ACTIVE
            #     to_parse = True
            # else:
            #     vehicle.relevance = RelevanceStatus.IRRELEVANT
            vehicle.relevance = RelevanceStatus.IRRELEVANT
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

            await db.commit()
            return to_parse

    except IntegrityError as e:
        if "unique constraint" in str(e).lower() and "vin" in str(e).lower():
            logger.info(f"Exception -----------> {e} for vin: {vehicle_data.vin}")
            return False
    except Exception as e:
        logger.info(f"Exception -----------> {e} for vin: {vehicle_data.vin}")
        return False


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
    db: AsyncSession,
    filters: Dict[str, Any],
    ordering,
    page: int,
    page_size: int
) -> Tuple[List[CarModel], int, int, Dict[str, Any]]:

    user_id = filters.get("user_id")

    def _norm_strs(values: Iterable[Any]) -> List[str]:
        return [v.lower() for v in values if isinstance(v, str)]

    def _str_in(field, values: Iterable[Any]):
        vals = _norm_strs(values)
        if not vals:
            return False  # зробити фільтр "ніщо не знайдено"
        return func.lower(field).in_(vals)

    def _int_in(field, values: Iterable[Any]):
        ints = [int(v) for v in values if isinstance(v, int) or (isinstance(v, str) and v.isdigit())]
        if not ints:
            return False
        return field.in_(ints)

    # liked як булевий вираз (без JOIN)
    liked_exists = exists(
        select(user_likes.c.car_id).where(
            (user_likes.c.car_id == CarModel.id) &
            (user_likes.c.user_id == user_id)
        )
    )

    today = datetime.utcnow().date()
    base_query = (
        select(CarModel)
        .options(
            selectinload(CarModel.photos),
            selectinload(CarModel.condition_assessments),
        )
        .filter(
            CarModel.relevance == RelevanceStatus.ACTIVE,
            CarModel.predicted_total_investments.isnot(None),
            CarModel.predicted_total_investments > 0,
            CarModel.suggested_bid.isnot(None),
            CarModel.suggested_bid > 0,
            or_(            
                CarModel.date.isnot(None),
                CarModel.auction_name == "Buynow")
        )
    )

    # ---- ConditionAssessments: фільтруємо через EXISTS, а завантаження робимо selectinload ----
    cond_values = filters.get("condition_assessments")
    if cond_values:
        base_query = base_query.filter(
            exists(
                select(1)
                .select_from(ConditionAssessmentModel)
                .where(
                    (ConditionAssessmentModel.car_id == CarModel.id) &
                    (ConditionAssessmentModel.issue_description.in_(cond_values))
                )
            )
        )
        # обмежуємо самі завантажені рядки у відношенні (щоб у car.condition_assessments були тільки потрібні)
        base_query = base_query.options(
            with_loader_criteria(
                ConditionAssessmentModel,
                ConditionAssessmentModel.issue_description.in_(cond_values),
                include_aliases=True,
            )
        )
    else:
        default_excluded = ["Biohazard/Chemical", "Water/Flood", "Rejected Repair"]
        base_query = base_query.filter(
            ~exists(
                select(1)
                .select_from(ConditionAssessmentModel)
                .where(
                    (ConditionAssessmentModel.car_id == CarModel.id) &
                    (ConditionAssessmentModel.issue_description.in_(default_excluded))
                )
            )
        )

    # ---- ZIP search ----
    if filters.get("zip_search"):
        zip_code, radius = filters["zip_search"]
        zip_row = await db.execute(select(USZipModel).where(USZipModel.zip == zip_code))
        zip_data = zip_row.scalar_one_or_none()
        if not zip_data:
            raise HTTPException(status_code=404, detail=f"ZIP {zip_code} not found")

        lat1, lon1 = float(zip_data.lat), float(zip_data.lng)

        distance_expr = (
            3958.8
            * func.acos(
                func.sin(func.radians(bindparam("lat1"))) * func.sin(func.radians(USZipModel.lat))
                + func.cos(func.radians(bindparam("lat1"))) * func.cos(func.radians(USZipModel.lat))
                * func.cos(func.radians(USZipModel.lng) - func.radians(bindparam("lon1")))
            )
        ).label("distance")

        zip_subq = (
            select(USZipModel.copart_name, USZipModel.iaai_name)
            .where(distance_expr <= bindparam("radius"))
        ).params(lat1=lat1, lon1=lon1, radius=radius)

        zip_result = await db.execute(zip_subq)
        zip_names: set[str] = set()
        for copart, iaai in zip_result.all():
            if copart:
                zip_names.add(copart.lower())
            if iaai:
                zip_names.add(iaai.lower())

        if zip_names:
            base_query = base_query.filter(_str_in(CarModel.location, zip_names))
        else:
            base_query = base_query.filter(False)  # свідомо "порожньо"
        logger.debug(f"Nearby location -----> {zip_names}")

    # ---- string filters ----
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
        values = filters.get(field_name)
        if values:
            base_query = base_query.filter(_str_in(column, values))
        elif field_name == "fuel_type":
            base_query = base_query.filter(CarModel.fuel_type != "Hybrid")

    # ---- integer filters ----
    if filters.get("engine_cylinder"):
        base_query = base_query.filter(_int_in(CarModel.engine_cylinder, filters["engine_cylinder"]))

    # ---- numeric ranges ----
    for key, col in {
        "mileage_min": CarModel.mileage,
        "predicted_profit_margin_min": CarModel.profit_margin,
        "predicted_roi_min": CarModel.roi,
        "min_owners_count": CarModel.owners,
        "min_accident_count": CarModel.accident_count,
        "min_year": CarModel.year,
    }.items():
        val = filters.get(key)
        if val is not None:
            base_query = base_query.filter(col >= val)

    for key, col in {
        "mileage_max": CarModel.mileage,
        "predicted_profit_margin_max": CarModel.profit_margin,
        "predicted_roi_max": CarModel.roi,
        "max_owners_count": CarModel.owners,
        "max_accident_count": CarModel.accident_count,
        "max_year": CarModel.year,
    }.items():
        val = filters.get(key)
        if val is not None:
            base_query = base_query.filter(col <= val)

    # ---- date range ----
    if filters.get("date_from"):
        date_from = filters["date_from"]
        if isinstance(date_from, str):
            date_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        base_query = base_query.filter(CarModel.date >= datetime.combine(date_from, time.min))

    if filters.get("date_to"):
        date_to = filters["date_to"]
        if isinstance(date_to, str):
            date_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        base_query = base_query.filter(CarModel.date <= datetime.combine(date_to, time.max))

    # ---- recommended_only ----
    if filters.get("recommended_only"):
        base_query = base_query.filter(CarModel.recommendation_status == RecommendationStatus.RECOMMENDED)

    # ---- liked=True як фільтр ----
    if filters.get("liked"):
        if user_id is None:
            raise ValueError("user_id is required when filtering by liked=True")
        base_query = base_query.filter(liked_exists)

    # ---- count без дублікатів ----
    count_subq = base_query.with_only_columns(CarModel.id).distinct().subquery()
    total_count = await db.scalar(select(func.count()).select_from(count_subq))
    total_pages = (total_count + page_size - 1) // page_size if page_size > 0 else 1

    # ---- агрегації по distinct id ----
    bids_info: Dict[str, Any] = {}
    if page == 1:
        stats_src = base_query.with_only_columns(CarModel.id, CarModel.current_bid).distinct().subquery()
        row = (await db.execute(
            select(
                func.min(stats_src.c.current_bid),
                func.max(stats_src.c.current_bid),
                func.avg(stats_src.c.current_bid),
            )
        )).one_or_none()
        if row:
            min_bid, max_bid, avg_bid = row
        else:
            min_bid = max_bid = 0
            avg_bid = 0.0
        bids_info = {
            "min_bid": min_bid,
            "max_bid": max_bid,
            "avg_bid": round(avg_bid or 0.0, 2),
            "total_count": total_count,
        }

    # ---- вибірка сторінки + булевий liked ----
    order_clause = ORDERING_MAP.get(ordering, desc(CarModel.created_at))
    page_query = (
        base_query
        .add_columns(liked_exists.label("liked"))
        .order_by(order_clause)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    res = await db.execute(page_query)
    rows = res.all()

    vehicles: List[CarModel] = []
    for car, liked in rows:
        car.liked = bool(liked)
        vehicles.append(car)

    return vehicles, total_count, total_pages, bids_info



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

        # if current_user.has_role(UserRoleEnum.ADMIN):
        #     query = query.filter(
        #         ~CarModel.car_status.in_(
        #             [
        #                 CarStatus.NEW,
        #             ]
        #         )
        #     )
        # else:
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


async def bulk_save_vehicles(db: AsyncSession, vehicles: CarBulkCreateSchema) -> List[str]:
    """Bulk save vehicles and return skipped VINs."""
    skipped_vins = []
    for vehicle_data in vehicles.vehicles:
        success = await save_vehicle_with_photos(vehicle_data, vehicles.ivent, db)
        await db.commit()
        if not success:
            skipped_vins.append(vehicle_data.vin)
    return skipped_vins
