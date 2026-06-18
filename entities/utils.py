import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.vehicle import (
    CarInventoryInvestmentsModel,
    CarInventoryModel,
    CarInventoryInvestmentsType,
)

logger = logging.getLogger(__name__)


async def update_inventory_financials(db: AsyncSession, inventory_id: int):
    result = await db.execute(
        select(
            CarInventoryInvestmentsModel.investment_type,
            func.sum(CarInventoryInvestmentsModel.cost),
        )
        .where(CarInventoryInvestmentsModel.car_inventory_id == inventory_id)
        .group_by(CarInventoryInvestmentsModel.investment_type)
    )

    rows = result.all()

    logger.info("Inventory financials update for inventory_id=%s", inventory_id)
    logger.info("Raw investment rows: %s", rows)

    for investment_type, total_cost in rows:
        logger.info(
            "Investment type raw=%s | repr=%r | type=%s | total_cost=%s",
            investment_type,
            investment_type,
            type(investment_type),
            total_cost,
        )

        if hasattr(investment_type, "name"):
            logger.info("Investment type name=%s", investment_type.name)

        if hasattr(investment_type, "value"):
            logger.info("Investment type value=%s", investment_type.value)

    sums_by_type = dict(rows)

    logger.info("sums_by_type=%s", sums_by_type)
    logger.info(
        "sums_by_type keys=%s",
        [(key, type(key)) for key in sums_by_type.keys()],
    )

    update_values = {
        "parts_cost": sums_by_type.get(CarInventoryInvestmentsType.PARTS, 0),
        "maintenance": sums_by_type.get(CarInventoryInvestmentsType.MAINTENANCE, 0),
        "auction_fee": sums_by_type.get(CarInventoryInvestmentsType.AUCTION_FEE, 0),
        "transportation": sums_by_type.get(CarInventoryInvestmentsType.TRANSPORTATION, 0),
        "labor": sums_by_type.get(CarInventoryInvestmentsType.LABOR, 0),
        "additional_costs": sums_by_type.get(CarInventoryInvestmentsType.ADDITIONAL_COSTS, 0),
    }

    logger.info("update_values=%s", update_values)

    await db.execute(
        update(CarInventoryModel)
        .where(CarInventoryModel.id == inventory_id)
        .values(**update_values)
    )