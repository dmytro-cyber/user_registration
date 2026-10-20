"""Manual upsert transaction and enrichment publication."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle_upsert import write_vehicle_upsert
from domain.vehicle_ingestion import VehicleEnrichmentPublisher
from exceptions.vehicle_ingestion import VehicleUpsertError
from schemas.vehicle import CarUpsertSchema

logger = logging.getLogger(__name__)


async def upsert_vehicle(
    db: AsyncSession,
    vehicle_data: CarUpsertSchema,
    publisher: VehicleEnrichmentPublisher,
) -> str | None:
    try:
        should_parse = await write_vehicle_upsert(vehicle_data, db)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Vehicle upsert failed | vin=%s", vehicle_data.vin)
        raise VehicleUpsertError(str(exc)) from exc

    if not should_parse:
        return None
    try:
        return publisher.publish(
            {
                "vin": vehicle_data.vin,
                "car_name": vehicle_data.vehicle,
                "car_engine": vehicle_data.engine_title,
                "mileage": vehicle_data.mileage,
                "car_make": vehicle_data.make,
                "car_model": vehicle_data.model,
                "car_year": vehicle_data.year,
                "car_transmison": vehicle_data.transmision,
            }
        )
    except Exception:
        logger.exception("Celery send failed but vehicle saved | vin=%s", vehicle_data.vin)
        return None
