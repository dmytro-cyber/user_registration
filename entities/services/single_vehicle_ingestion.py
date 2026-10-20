"""Application scenarios for an individual vehicle snapshot."""

from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle import get_vehicle_by_vin, save_sale_history
from domain.vehicle_source import VehicleDataSource
from exceptions.vehicle_source import SavedVehicleNotFound
from models.vehicle import CarModel
from schemas.vehicle import CarBaseSchema
from services.vehicle import car_to_dict
from services.vehicle_snapshot import save_vehicle_snapshot


async def ingest_vehicle(vin: str, db: AsyncSession, source: VehicleDataSource) -> CarBaseSchema:
    snapshot = await source.fetch(vin)
    # Preserve the read-after-save behavior, including legacy write failures.
    await save_vehicle_snapshot(snapshot, "initial", db)
    vehicle = await get_vehicle_by_vin(db, vin, 1)
    if vehicle is None:
        raise SavedVehicleNotFound("Failed to retrieve saved vehicle")
    return CarBaseSchema.model_validate(car_to_dict(vehicle))


async def ingest_sales_history(vin: str, db: AsyncSession, source: VehicleDataSource) -> CarModel:
    snapshot = await source.fetch(vin)
    vehicle = await get_vehicle_by_vin(db, vin, 1)
    await save_sale_history(snapshot.sales_history, vehicle.id, db)
    await db.commit()
    return vehicle
