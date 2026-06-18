from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.vehicle import CarInventoryInvestmentsModel, CarInventoryModel, CarInventoryStatus


async def update_inventory_financials(db: AsyncSession, inventory_id: int):
    result = await db.execute(
        select(CarInventoryInvestmentsModel.investment_type, func.sum(CarInventoryInvestmentsModel.cost))
        .where(CarInventoryInvestmentsModel.car_inventory_id == inventory_id)
        .group_by(CarInventoryInvestmentsModel.investment_type)
    )
    sums_by_type = dict(result.all())

    update_values = {
        "parts_cost": sums_by_type.get(CarInventoryStatus.PARTS, 0),
        "maintenance": sums_by_type.get(CarInventoryStatus.MAINTENANCE, 0),
        "auction_fee": sums_by_type.get(CarInventoryStatus.AUCTION_FEE, 0),
        "transportation": sums_by_type.get(CarInventoryStatus.TRANSPORTATION, 0),
        "labor": sums_by_type.get(CarInventoryStatus.LABOR, 0),
        "additional_costs": sums_by_type.get(CarInventoryStatus.ADDITIONAL_COSTS, 0),
    }

    await db.execute(update(CarInventoryModel).where(CarInventoryModel.id == inventory_id).values(**update_values))
