"""Partial vehicle cost update with the existing commit/refresh boundary."""

from sqlalchemy.ext.asyncio import AsyncSession

from crud.vehicle_costs import apply_vehicle_costs, get_vehicle_for_cost_update
from exceptions.vehicle_costs import CostVehicleNotFoundError, EmptyVehicleCostsError, VehicleCostsPersistenceError


async def update_vehicle_costs(db: AsyncSession, *, car_id: int, fields: dict[str, float | None]) -> dict:
    car = await get_vehicle_for_cost_update(db, car_id)
    if car is None:
        raise CostVehicleNotFoundError()
    if not fields:
        raise EmptyVehicleCostsError()
    apply_vehicle_costs(car, fields)
    try:
        await db.commit()
        await db.refresh(car)
        return {
            "id": car.id,
            "maintenance": car.maintenance,
            "transportation": car.transportation,
            "labor": car.labor,
            "suggested_bid": car.suggested_bid,
        }
    except Exception as exc:
        await db.rollback()
        raise VehicleCostsPersistenceError() from exc
