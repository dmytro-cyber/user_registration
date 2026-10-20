"""Legacy task identities with direct application-service persistence."""

import logging

from core.integration_celery import app
from services import apicar_ingestion
from tasks.integrations import run_async

logger = logging.getLogger(__name__)


async def save_batch(payload):
    from core.celery_config import app as entities_app
    from db.session import session_scope
    from integrations.vehicle_tasks import CeleryVehicleEnrichmentPublisher
    from schemas.vehicle import CarBulkCreateSchema
    from services.vehicle_ingestion import bulk_save_vehicles

    async with session_scope() as db:
        return await bulk_save_vehicles(
            db, CarBulkCreateSchema.model_validate(payload), CeleryVehicleEnrichmentPublisher(entities_app)
        )


async def delete_batch(payload):
    from crud.vehicle import update_cars_relevance
    from db.session import session_scope

    async with session_scope() as db:
        try:
            await update_cars_relevance(payload, db)
        except Exception:
            await db.rollback()
            # The former task ignored HTTP error responses from bulk/delete.
            logger.exception("Failed to apply APICAR deletion batch")


@app.task(name="tasks.tasks.fetch_api_data")
def fetch_api_data(size=None, base_url=None):
    return apicar_ingestion.fetch_api_data(lambda payload: run_async(save_batch, payload), size, base_url)


@app.task(name="tasks.tasks.delete_vehicle")
def delete_vehicle():
    return apicar_ingestion.delete_vehicle(lambda payload: run_async(delete_batch, payload))
