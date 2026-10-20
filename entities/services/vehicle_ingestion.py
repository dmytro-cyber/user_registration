"""Coordinate a caller-sized vehicle import and its post-commit enrichment tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle_ingestion import CAR_COLUMNS, REQUIRED_CAR_COLUMNS, load_vehicle_batch_context, persist_vehicle_batch
from domain.vehicle_ingestion import VehicleBatchPlan, VehicleBatchResult, VehicleEnrichmentPublisher
from exceptions.vehicle_ingestion import VehicleBatchImportError
from services.vehicle_ingestion_planning import deduplicate_vehicles, prepare_vehicle_batch

if TYPE_CHECKING:
    from schemas.vehicle import CarBulkCreateSchema


async def bulk_save_vehicles(
    db: AsyncSession,
    payload: CarBulkCreateSchema,
    publisher: VehicleEnrichmentPublisher,
) -> VehicleBatchResult:
    """Commit parent and child data together, then publish in request order.

    Publication retains the existing failure policy: errors after commit are
    reported to the caller even though committed data and earlier publications
    cannot be undone by rollback. No distributed transaction is implied.
    """
    try:
        vehicles = deduplicate_vehicles(payload.vehicles or [])
        if vehicles:
            existing, filters = await load_vehicle_batch_context(db, [vehicle.vin for vehicle in vehicles])
            plan = prepare_vehicle_batch(
                vehicles,
                payload.ivent,
                existing,
                filters,
                CAR_COLUMNS,
                REQUIRED_CAR_COLUMNS,
            )
            await persist_vehicle_batch(db, plan, existing)
        else:
            plan = VehicleBatchPlan([], [], [], [], 0, 0)

        await db.commit()
        for task in plan.enrichment_tasks:
            publisher.publish(task)
        return plan.result()
    except Exception as exc:
        # Preserve the original behavior if rollback itself fails: let it propagate.
        await db.rollback()
        raise VehicleBatchImportError(str(exc)) from exc
