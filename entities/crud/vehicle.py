import logging
from datetime import datetime, time, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import and_, asc, bindparam, case, delete, desc, exists, func, literal_column, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, noload, selectinload, with_loader_criteria
from sqlalchemy.sql import over

from core.dependencies import get_s3_storage_client
from core.setup import match_and_update_location
from models.admin import FilterModel
from models.user import UserModel, UserRoleEnum, user_likes
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
from schemas.vehicle import CarBulkCreateSchema, CarCreateSchema, CarUpsertSchema

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


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


async def save_sale_history(sale_history_data: List[CarCreateSchema], car_id: int, db: AsyncSession) -> None:
    """Save sales history for a vehicle."""
    if len(sale_history_data) >= 4:
        logger.debug(
            f"More than 3 sales history records provided for car ID {car_id}. Car will not be recomendet for purchase."
        )
        car = await get_vehicle_by_id(db, car_id)
        car.recommendation_status = RecommendationStatus.NOT_RECOMMENDED
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


async def save_vehicle_with_photos(vehicle_data: CarCreateSchema, ivent: str, db: AsyncSession) -> bool:
    """Save a single vehicle and its photos. Update all fields and photos if vehicle already exists."""
    try:
        to_parse = False
        existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin)
        if existing_vehicle:
            if existing_vehicle.is_manually_upserted:
                if vehicle_data.current_bid is not None:
                    existing_vehicle.current_bid = vehicle_data.current_bid
                    if (
                        existing_vehicle.suggested_bid is not None
                        and vehicle_data.current_bid > existing_vehicle.suggested_bid
                    ):
                        existing_vehicle.recommendation_status = RecommendationStatus.NOT_RECOMMENDED

                    await db.commit()

                return False
            if existing_vehicle.relevance == RelevanceStatus.ACTIVE:
                if existing_vehicle.is_checked == False and existing_vehicle.attempts < 3:
                    to_parse = True
            elif existing_vehicle.relevance == RelevanceStatus.ARCHIVAL and ivent == "update":
                query = select(FilterModel).where(
                    FilterModel.make == vehicle_data.make,
                    FilterModel.model == vehicle_data.model,
                    FilterModel.year_from <= vehicle_data.year,
                    FilterModel.year_to >= vehicle_data.year,
                    FilterModel.odometer_max >= vehicle_data.mileage
                )
                filter_ex = await db.execute(query)
                filter_res = filter_ex.scalars().one_or_none()
                if filter_res:
                    existing_vehicle.relevance = RelevanceStatus.ACTIVE
                    existing_vehicle.attempts = 0
                    existing_vehicle.is_checked = False

                    db.add(existing_vehicle)

                    to_parse = True
                else:
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

            query = select(FilterModel).where(
                FilterModel.make == vehicle_data.make,
                or_(
                    FilterModel.model == vehicle_data.model,
                    FilterModel.model.is_(None)
                ),
                FilterModel.year_from <= vehicle_data.year,
                FilterModel.year_to >= vehicle_data.year,
                FilterModel.odometer_max >= vehicle_data.mileage
            )
            filter_ex = await db.execute(query)
            filter_res = filter_ex.scalars().one_or_none()
            if filter_res:
                vehicle.relevance = RelevanceStatus.ACTIVE
                to_parse = True
            else:
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


async def get_vehicle_by_vin(
    db: AsyncSession,
    vin: str,
    current_user_id: Optional[int] = None,
) -> Optional[CarModel]:
    """
    Fetch a car by VIN with safe loader strategy:
    - noload('*') disables any accidental lazy loading for ALL relationships.
    - selectinload(...) eagerly loads only the relationships we plan to touch.
    - 'liked' is computed via EXISTS on the association table, not via lazy M2M.
    - finally, the entity is expunged (detached) to make accidental lazy loads impossible.
    """
    stmt = (
        select(CarModel)
        .options(
            # disable any other relationships (no SQL will be emitted when accessed)
            noload('*'),
            # explicitly load only what serializer will read
            selectinload(CarModel.photos),
            selectinload(CarModel.photos_hd),
            selectinload(CarModel.condition_assessments),
            selectinload(CarModel.sales_history),
        )
        .where(CarModel.vin == vin)
        .limit(1)
    )
    res = await db.execute(stmt)
    car = res.scalars().first()
    if not car:
        return None

    # Compute 'liked' via EXISTS on the link table
    if current_user_id:
        liked_q = select(
            exists().where(
                (user_likes.c.user_id == current_user_id) &
                (user_likes.c.car_id == car.id)
            )
        )
        liked = (await db.execute(liked_q)).scalar()
        car.liked = bool(liked)
    else:
        car.liked = False

    # Detach the instance to prevent *any* chance of further lazy loads
    # (safe to call on AsyncSession)
    db.expunge(car)
    return car


async def save_vehicle(db: AsyncSession, vehicle_data: CarCreateSchema) -> Optional[CarModel]:
    """Save a vehicle to the database if it doesn't already exist."""
    existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin, 1)
    if existing_vehicle:
        return None

    db_vehicle = CarModel(**vehicle_data.dict(exclude_unset=True))
    db.add(db_vehicle)
    return db_vehicle


async def get_filtered_vehicles(
    db: "AsyncSession",
    filters: Dict[str, Any],
    ordering,
    page: int,
    page_size: int
) -> Tuple[List["CarModel"], int, int, Dict[str, Any]]:
    """
    Return vehicles with full filtering, deterministic ordering, and de-duplicated pagination.

    Strategy to avoid duplicates:
      1) Build a filtered SELECT over CarModel.id only (no eager loads) -> DISTINCT ids subquery.
      2) ORDER and paginate those ids.
      3) Fetch full CarModel rows for the paginated ids (with eager loads) + computed "liked" flag.

    This guarantees: count == size of the DISTINCT id set, and page results have unique cars.
    """

    def _norm_strs(values: Iterable[Any]) -> List[str]:
        """Lowercase only string values."""
        return [v.lower() for v in values if isinstance(v, str)]

    def _str_in(field, values: Iterable[Any]):
        """Case-insensitive IN against a list of strings; returns SQLA clause or False if empty."""
        vals = _norm_strs(values)
        if not vals:
            return False
        return func.lower(field).in_(vals)

    def _int_in(field, values: Iterable[Any]):
        """IN against a list that may contain ints or numeric strings; returns clause or False."""
        ints = [int(v) for v in values if isinstance(v, int) or (isinstance(v, str) and v.isdigit())]
        if not ints:
            return False
        return field.in_(ints)

    user_id = filters.get("user_id")

    # liked EXISTS helper (re-used both for filtering and projection)
    liked_exists = exists(
        select(user_likes.c.car_id).where(
            (user_likes.c.car_id == CarModel.id) &
            (user_likes.c.user_id == user_id)
        )
    )

    # Base: filter for valid/active sellable cars
    base_ids = (
        select(CarModel.id)
        .filter(
            CarModel.relevance == RelevanceStatus.ACTIVE,
            CarModel.predicted_total_investments.isnot(None),
            CarModel.predicted_total_investments > 0,
            CarModel.suggested_bid.isnot(None),
            CarModel.suggested_bid > 0,
            or_(
                CarModel.date.isnot(None),
                CarModel.auction_name == "Buynow"
            ),
        )
    )

    # ---- ConditionAssessments via EXISTS (no JOIN → no duplication) ----
    # Also prepare optional loader criteria to restrict loaded related rows.
    cond_values = filters.get("condition_assessments")
    loader_options = [
        selectinload(CarModel.photos),
        selectinload(CarModel.condition_assessments),
    ]
    if cond_values:
        base_ids = base_ids.filter(
            exists(
                select(1)
                .select_from(ConditionAssessmentModel)
                .where(
                    (ConditionAssessmentModel.car_id == CarModel.id) &
                    (ConditionAssessmentModel.issue_description.in_(cond_values))
                )
            )
        )
        loader_options.append(
            with_loader_criteria(
                ConditionAssessmentModel,
                ConditionAssessmentModel.issue_description.in_(cond_values),
                include_aliases=True,
            )
        )
    else:
        default_excluded = ["Biohazard/Chemical", "Water/Flood", "Rejected Repair"]
        base_ids = base_ids.filter(
            ~exists(
                select(1)
                .select_from(ConditionAssessmentModel)
                .where(
                    (ConditionAssessmentModel.car_id == CarModel.id) &
                    (ConditionAssessmentModel.issue_description.in_(default_excluded))
                )
            )
        )

    # ---- ZIP proximity search (Copart/IAAI yard names) ----
    if filters.get("zip_search"):
        zip_code, radius = filters["zip_search"]
        zip_row = await db.execute(select(USZipModel).where(USZipModel.zip == zip_code))
        zip_data = zip_row.scalar_one_or_none()
        if not zip_data:
            raise HTTPException(status_code=404, detail=f"ZIP {zip_code} not found")

        lat1, lon1 = float(zip_data.lat), float(zip_data.lng)
        dot = (
            func.sin(func.radians(bindparam("lat1"))) * func.sin(func.radians(USZipModel.lat)) +
            func.cos(func.radians(bindparam("lat1"))) * func.cos(func.radians(USZipModel.lat)) *
            func.cos(func.radians(USZipModel.lng) - func.radians(bindparam("lon1")))
        )

        clamped = func.least(1.0, func.greatest(-1.0, dot))

        distance_expr = (3958.8 * func.acos(clamped)).label("distance")

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
            base_ids = base_ids.filter(_str_in(CarModel.location, zip_names))
            logger.info(f"ZIP ------> {zip_names}")
        else:
            # Force empty result if within radius there are no known yards.
            base_ids = base_ids.filter(False)

    # ---- String filters (case-insensitive) ----
    for field_name, column in {
        "make": CarModel.make,
        "body_style": CarModel.body_style,
        "vehicle_type": CarModel.vehicle_type,
        "transmission": CarModel.transmision,   # note: model field is 'transmision'
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
            base_ids = base_ids.filter(_str_in(column, values))
        elif field_name == "fuel_type":
            # Default rule from your original code: exclude Hybrids when no explicit fuel_type given
            base_ids = base_ids.filter(CarModel.fuel_type != "Hybrid")

    # ---- Integer IN filters ----
    if filters.get("engine_cylinder"):
        base_ids = base_ids.filter(_int_in(CarModel.engine_cylinder, filters["engine_cylinder"]))

    # ---- Numeric ranges (>= mins) ----
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
            base_ids = base_ids.filter(col >= val)

    # ---- Numeric ranges (<= max) ----
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
            base_ids = base_ids.filter(col <= val)

    # ---- Date range (inclusive day bounds) ----
    if filters.get("date_from"):
        date_from = filters["date_from"]
        if isinstance(date_from, str):
            date_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        base_ids = base_ids.filter(CarModel.date >= datetime.combine(date_from, time.min))

    if filters.get("date_to"):
        date_to = filters["date_to"]
        if isinstance(date_to, str):
            date_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        base_ids = base_ids.filter(CarModel.date <= datetime.combine(date_to, time.max))

    # ---- recommended_only ----
    if filters.get("recommended_only"):
        base_ids = base_ids.filter(CarModel.recommendation_status == RecommendationStatus.RECOMMENDED)

    # ---- liked=True ----
    if filters.get("liked"):
        if user_id is None:
            raise ValueError("user_id is required when filtering by liked=True")
        base_ids = base_ids.filter(liked_exists)
    if filters.get("title"):
        is_salvage = filters.get("title")
        if is_salvage and len(is_salvage) == 1 and "Salvage" in is_salvage:
            base_ids = base_ids.filter(CarModel.is_salvage == True)
        elif is_salvage and len(is_salvage) == 1 and "Clean" in is_salvage:
            base_ids = base_ids.filter(CarModel.is_salvage == False)
    # ----------------------------
    # COUNT over DISTINCT ids
    # ----------------------------
    distinct_ids_sq = base_ids.distinct().subquery()
    total_count = await db.scalar(select(func.count()).select_from(distinct_ids_sq))
    total_pages = (total_count + page_size - 1) // page_size if page_size > 0 else 1

    # Determine the ordering
    ORDERING = globals().get("ORDERING_MAP", {})
    order_clause = ORDERING.get(ordering, desc(CarModel.created_at))

    # ----------------------------
    # Page of ids: ORDER + OFFSET/LIMIT (stable ordering)
    # ----------------------------
    paged_ids_sq = (
        select(distinct_ids_sq.c.id)
        .join(CarModel, CarModel.id == distinct_ids_sq.c.id)
        .order_by(order_clause, CarModel.id)  # stable tie-breaker
        .offset(max(page - 1, 0) * page_size)
        .limit(page_size)
    ).subquery()

    # ----------------------------
    # Final fetch: full rows + liked flag (no duplicates)
    # ----------------------------
    page_query = (
        select(CarModel, liked_exists.label("liked"))
        .where(CarModel.id.in_(select(paged_ids_sq.c.id)))
        .order_by(order_clause, CarModel.id)
        .options(*loader_options)
    )

    res = await db.execute(page_query)
    rows = res.all()

    vehicles: List[CarModel] = []
    for car, liked in rows:
        # attach liked flag for convenience
        setattr(car, "liked", bool(liked))
        vehicles.append(car)

    # ----------------------------
    # Aggregates (first page only) over DISTINCT ids
    # ----------------------------
    bids_info: Dict[str, Any] = {}
    if page == 1:
        agg_row = (await db.execute(
            select(
                func.min(CarModel.current_bid),
                func.max(CarModel.current_bid),
                func.avg(CarModel.current_bid),
            ).where(CarModel.id.in_(select(distinct_ids_sq.c.id)))
        )).one_or_none()

        if agg_row:
            min_bid, max_bid, avg_bid = agg_row
        else:
            min_bid = max_bid = 0
            avg_bid = 0.0

        bids_info = {
            "min_bid": min_bid,
            "max_bid": max_bid,
            "avg_bid": round(avg_bid or 0.0, 2),
            "total_count": total_count,
        }

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


def is_vehicle_sellable(
    car: "CarModel",
    excluded_conditions: Iterable[str] = (
        "Biohazard/Chemical",
        "Water/Flood",
        "Rejected Repair",
    ),
) -> bool:
    """
    Check whether a car passes base filters.
    """

    now = datetime.now(timezone.utc)

    # ---- Relevance ----
    if car.relevance != RelevanceStatus.ACTIVE:
        return False

    # ---- Investments & bid ----
    if not car.predicted_total_investments or car.predicted_total_investments <= 0:
        return False

    if not car.suggested_bid or car.suggested_bid <= 0:
        return False

    # ---- Date / BuyNow logic ----
    if car.auction_name == "Buynow":
        pass  # always allowed
    else:
        if not car.date:
            return False
        if car.date < now:
            return False

    # ---- Condition assessments (optional, if loaded) ----
    if getattr(car, "condition_assessments", None):
        for ca in car.condition_assessments:
            if ca.issue_description in excluded_conditions:
                return False

    return True


async def upsert_vehicle(vehicle_data: CarUpsertSchema, db: AsyncSession) -> bool:
    """Save a single vehicle and its photos. Update all fields and photos if vehicle already exists."""
    try:
        existing_vehicle = await get_vehicle_by_vin(db, vehicle_data.vin)
        if existing_vehicle:
            existing_vehicle.relevance == RelevanceStatus.ACTIVE
            existing_vehicle.is_checked = False
            existing_vehicle.attempts = 0


            
            # logger.info(f"Vehicle with VIN {vehicle_data.vin} already exists. Updating data...")
            existing_vehicle.recommendation_status = RecommendationStatus.RECOMMENDED
            existing_vehicle.recommendation_status_reasons = None
            existing_vehicle.is_manually_upserted = True
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

            await db.commit()

            return True, "success"

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
            vehicle.is_manually_upserted = True
            vehicle.relevance = RelevanceStatus.ACTIVE

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
            return True, "success"

    except IntegrityError as e:
        if "unique constraint" in str(e).lower() and "vin" in str(e).lower():
            logger.info(f"Exception -----------> {e} for vin: {vehicle_data.vin}")
            return False, e
    except Exception as e:
        logger.info(f"Exception -----------> {e} for vin: {vehicle_data.vin}")
        return False, e