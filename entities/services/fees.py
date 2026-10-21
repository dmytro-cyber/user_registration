"""Manual fee workflows with the existing transaction and refresh policy."""

from sqlalchemy.ext.asyncio import AsyncSession

from crud.fees import add_fee, apply_fee_fields, get_fee, list_fees, remove_fee
from exceptions.fees import FeeNotFound
from models.vehicle import FeeModel
from schemas.vehicle import FeeCreate, FeeUpdate


async def create_manual_fee(db: AsyncSession, payload: FeeCreate) -> FeeModel:
    try:
        fee = add_fee(db, payload.model_dump())
        await db.commit()
        await db.refresh(fee)
        return fee
    except Exception:
        await db.rollback()
        raise


async def list_manual_fees(db: AsyncSession) -> list[FeeModel]:
    return await list_fees(db)


async def update_manual_fee(db: AsyncSession, fee_id: int, payload: FeeUpdate) -> FeeModel:
    try:
        fee = await get_fee(db, fee_id)
        if fee is None:
            raise FeeNotFound()
        apply_fee_fields(fee, payload.model_dump(exclude_unset=True))
        await db.commit()
        await db.refresh(fee)
        return fee
    except Exception:
        await db.rollback()
        raise


async def delete_manual_fee(db: AsyncSession, fee_id: int) -> None:
    try:
        fee = await get_fee(db, fee_id)
        if fee is None:
            raise FeeNotFound()
        await remove_fee(db, fee)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
