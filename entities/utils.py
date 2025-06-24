from models.vehicle import CarInventoryInvestmentsModel, CarInventoryModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession


async def update_inventory_financials(db: AsyncSession, inventory_id: int):
    result = await db.execute(
        select(
            CarInventoryInvestmentsModel.investment_type,
            func.sum(CarInventoryInvestmentsModel.cost)
        ).where(CarInventoryInvestmentsModel.car_inventory_id == inventory_id)
        .group_by(CarInventoryInvestmentsModel.investment_type)
    )
    sums_by_type = dict(result.all())

    update_values = {
        "vehicle_cost": sums_by_type.get("Vehicle Cost", 0),
        "parts_cost": sums_by_type.get("Parts Cost", 0),
        "maintenance": sums_by_type.get("Maintenance", 0),
        "auction_fee": sums_by_type.get("Auction Fee", 0),
        "transportation": sums_by_type.get("Transportation", 0),
        "labor": sums_by_type.get("Labor", 0),
        "additional_costs": sums_by_type.get("Additional Costs", 0),
    }

    await db.execute(
        update(CarInventoryModel)
        .where(CarInventoryModel.id == inventory_id)
        .values(**update_values)
    )
