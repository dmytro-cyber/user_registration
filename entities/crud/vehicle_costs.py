"""Vehicle cost persistence; transaction ownership belongs to the service."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.vehicle import CarModel


async def get_vehicle_for_cost_update(db: AsyncSession, car_id: int) -> CarModel | None:
    result = await db.execute(select(CarModel).where(CarModel.id == car_id).with_for_update())
    return result.scalars().first()


def apply_vehicle_costs(car: CarModel, fields: dict[str, float | None]) -> None:
    for key, value in fields.items():
        setattr(car, key, value)
    if car.predicted_total_investments is not None:
        car.suggested_bid = car.predicted_total_investments - car.sum_of_investments
