"""Manual fee reads and writes; callers own commit and rollback."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.vehicle import FeeModel


def add_fee(db: AsyncSession, values: dict[str, Any]) -> FeeModel:
    fee = FeeModel(**values)
    db.add(fee)
    return fee


async def get_fee(db: AsyncSession, fee_id: int) -> FeeModel | None:
    return await db.get(FeeModel, fee_id)


async def list_fees(db: AsyncSession) -> list[FeeModel]:
    return (await db.execute(select(FeeModel))).scalars().all()


def apply_fee_fields(fee: FeeModel, values: dict[str, Any]) -> None:
    for field, value in values.items():
        setattr(fee, field, value)


async def remove_fee(db: AsyncSession, fee: FeeModel) -> None:
    await db.delete(fee)
