"""Filter selection and relevance writes; application services own commit."""

from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domain.vehicle_filters import FilterDeletionResult
from models.admin import FilterModel
from models.vehicle import CarModel, CarStatus, RelevanceStatus


async def insert_vehicle_filter(db: AsyncSession, values: dict[str, Any], updated_at: datetime) -> FilterModel:
    db_filter = FilterModel(**values)
    db_filter.updated_at = updated_at
    db.add(db_filter)
    await db.flush()
    return db_filter


async def activate_vehicles_for_created_filter(db: AsyncSession, db_filter: FilterModel) -> None:
    conditions = [
        CarModel.make == db_filter.make,
        CarModel.year >= (db_filter.year_from or 0),
        CarModel.year <= (db_filter.year_to or 3000),
        CarModel.mileage >= (db_filter.odometer_min or 0),
        CarModel.mileage <= (db_filter.odometer_max or 10_000_000),
    ]
    if db_filter.model is not None:
        conditions.append(CarModel.model == db_filter.model)
    await db.execute(
        update(CarModel)
        .where(and_(*conditions))
        .values(relevance=RelevanceStatus.ACTIVE)
        .execution_options(synchronize_session=False)
    )


def build_updated_filter_conditions(filter_obj):
    conditions = []

    if filter_obj.make:
        conditions.append(CarModel.make == filter_obj.make)
    if filter_obj.model:
        conditions.append(CarModel.model == filter_obj.model)
    if filter_obj.year_from:
        conditions.append(CarModel.year >= filter_obj.year_from)
    if filter_obj.year_to:
        conditions.append(CarModel.year <= filter_obj.year_to)
    if filter_obj.odometer_min:
        conditions.append(CarModel.mileage >= filter_obj.odometer_min)
    if filter_obj.odometer_max:
        conditions.append(CarModel.mileage <= filter_obj.odometer_max)

    return conditions


async def get_vehicle_filter(db: AsyncSession, filter_id: int) -> FilterModel | None:
    result = await db.execute(select(FilterModel).where(FilterModel.id == filter_id))
    return result.scalar_one_or_none()


async def matching_updated_filter_vehicle_ids(db: AsyncSession, db_filter: FilterModel) -> set[int]:
    result = await db.execute(select(CarModel.id).where(*build_updated_filter_conditions(db_filter)))
    return {row[0] for row in result.all()}


async def adjust_updated_filter_relevance(db: AsyncSession, to_activate: set[int], to_irrelevant: set[int]) -> None:
    if to_activate:
        await db.execute(
            update(CarModel)
            .where(
                CarModel.id.in_(to_activate),
                CarModel.relevance == RelevanceStatus.IRRELEVANT,
            )
            .values(relevance=RelevanceStatus.ACTIVE)
        )
    if to_irrelevant:
        await db.execute(
            update(CarModel)
            .where(
                CarModel.id.in_(to_irrelevant),
                CarModel.relevance == RelevanceStatus.ACTIVE,
                CarModel.car_status != CarStatus.WON,
            )
            .values(relevance=RelevanceStatus.IRRELEVANT)
        )
        await db.execute(
            delete(CarModel).where(
                CarModel.id.in_(to_irrelevant),
                CarModel.relevance == RelevanceStatus.ARCHIVAL,
                CarModel.car_status != CarStatus.WON,
            )
        )


async def adjust_vehicles_for_deleted_filter(db: AsyncSession, db_filter: FilterModel) -> FilterDeletionResult:
    base_filter = and_(
        CarModel.make == db_filter.make,
        CarModel.model == db_filter.model,
        CarModel.year >= (db_filter.year_from or 0),
        CarModel.year <= (db_filter.year_to or 3000),
        CarModel.mileage >= (db_filter.odometer_min or 0),
        CarModel.mileage <= (db_filter.odometer_max or 10_000_000),
    )

    archived_query = select(CarModel.id).where(
        and_(
            base_filter,
            CarModel.relevance == RelevanceStatus.ARCHIVAL,
            CarModel.car_status != CarStatus.WON,
        )
    )
    archived_ids = (await db.execute(archived_query)).scalars().all()

    if archived_ids:
        await db.execute(
            delete(CarModel).where(
                CarModel.id.in_(archived_ids),
                CarModel.car_status != CarStatus.WON,
            )
        )

    active_query = select(CarModel.id).where(
        and_(
            base_filter,
            CarModel.relevance == RelevanceStatus.ACTIVE,
            CarModel.car_status != CarStatus.WON,
        )
    )
    active_ids = (await db.execute(active_query)).scalars().all()

    if active_ids:
        await db.execute(
            update(CarModel)
            .where(CarModel.id.in_(active_ids), CarModel.car_status != CarStatus.WON)
            .values(relevance=RelevanceStatus.IRRELEVANT)
        )

    return FilterDeletionResult(len(archived_ids), len(active_ids))


async def remove_vehicle_filter(db: AsyncSession, db_filter: FilterModel) -> None:
    await db.delete(db_filter)
