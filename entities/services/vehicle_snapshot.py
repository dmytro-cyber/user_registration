"""Transaction boundary for a vehicle snapshot and all of its child records."""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle_snapshot import persist_vehicle_snapshot
from domain.vehicle_ingestion import VehicleSnapshotSaveResult
from schemas.vehicle import CarCreateSchema

logger = logging.getLogger(__name__)


async def save_vehicle_snapshot(
    vehicle_data: CarCreateSchema,
    event: str,
    db: AsyncSession,
) -> VehicleSnapshotSaveResult:
    try:
        needs_enrichment = await persist_vehicle_snapshot(vehicle_data, event, db)
        await db.commit()
        return VehicleSnapshotSaveResult(saved=True, needs_enrichment=needs_enrichment)
    except IntegrityError as exc:
        await db.rollback()
        if "unique constraint" in str(exc).lower() and "vin" in str(exc).lower():
            logger.info("Duplicate vehicle snapshot | vin=%s", vehicle_data.vin)
        else:
            logger.exception("Snapshot integrity error | vin=%s", vehicle_data.vin)
        return VehicleSnapshotSaveResult(saved=False)
    except Exception:
        await db.rollback()
        logger.exception("Snapshot save failed | vin=%s", vehicle_data.vin)
        return VehicleSnapshotSaveResult(saved=False)
