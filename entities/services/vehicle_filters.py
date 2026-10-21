"""Filter write workflows with their existing transaction boundaries."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle_filters import (
    activate_vehicles_for_created_filter,
    adjust_updated_filter_relevance,
    adjust_vehicles_for_deleted_filter,
    get_vehicle_filter,
    insert_vehicle_filter,
    matching_updated_filter_vehicle_ids,
    remove_vehicle_filter,
)
from domain.filter_kickoff import FilterUpdateKickoffPublisher
from domain.vehicle_filters import FilterDeletionResult
from exceptions.vehicle_filters import VehicleFilterNotFound
from models.admin import FilterModel
from schemas.admin import FilterCreate, FilterUpdate
from services.filter_kickoff_queue import enqueue_filter_kickoff


async def create_filter_and_schedule(db: AsyncSession, payload: FilterCreate) -> FilterModel:
    try:
        db_filter = await insert_vehicle_filter(db, payload.dict(exclude_unset=True), datetime.now())
        await activate_vehicles_for_created_filter(db, db_filter)
        await enqueue_filter_kickoff(db=db, filter_id=db_filter.id)
        await db.commit()
        await db.refresh(db_filter)
        return db_filter
    except Exception:
        await db.rollback()
        raise


async def update_filter_and_schedule(
    db: AsyncSession,
    filter_id: int,
    payload: FilterUpdate,
    publisher: FilterUpdateKickoffPublisher,
) -> dict[str, str]:
    try:
        db_filter = await get_vehicle_filter(db, filter_id)
        if db_filter is None:
            raise VehicleFilterNotFound()
        old_ids = await matching_updated_filter_vehicle_ids(db, db_filter)
        for key, value in payload.dict(exclude_unset=True).items():
            setattr(db_filter, key, value)
        await db.commit()
        await db.refresh(db_filter)
        new_ids = await matching_updated_filter_vehicle_ids(db, db_filter)
        await adjust_updated_filter_relevance(db, new_ids - old_ids, old_ids - new_ids)
        await db.commit()
        publisher.publish(db_filter.id)
        return {"detail": "Filter updated, relevance adjusted, kickoff scheduled"}
    except Exception:
        await db.rollback()
        raise


async def delete_filter_and_adjust(db: AsyncSession, filter_id: int) -> FilterDeletionResult:
    try:
        db_filter = await get_vehicle_filter(db, filter_id)
        if db_filter is None:
            raise VehicleFilterNotFound()
        result = await adjust_vehicles_for_deleted_filter(db, db_filter)
        await remove_vehicle_filter(db, db_filter)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise
