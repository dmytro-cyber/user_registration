from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.dependencies import get_current_user
from db.session import get_db
from models.vehicle import FeeModel
from models.user import UserModel
from schemas.vehicle import FeeCreate, FeeUpdate, FeeRead

router = APIRouter(prefix="/fees")


@router.post("/", response_model=FeeRead, status_code=status.HTTP_201_CREATED)
async def create_fee(
    payload: FeeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    fee = FeeModel(**payload.model_dump())
    db.add(fee)
    await db.commit()
    await db.refresh(fee)
    return fee


@router.get("/", response_model=List[FeeRead])
async def list_fees(
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    result = await db.execute(select(FeeModel))
    return result.scalars().all()


@router.put("/{fee_id}", response_model=FeeRead)
async def update_fee(
    fee_id: int,
    payload: FeeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    fee = await db.get(FeeModel, fee_id)
    if not fee:
        raise HTTPException(status_code=404, detail="Fee not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(fee, field, value)

    await db.commit()
    await db.refresh(fee)
    return fee


@router.delete("/{fee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fee(
    fee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    fee = await db.get(FeeModel, fee_id)
    if not fee:
        raise HTTPException(status_code=404, detail="Fee not found")

    await db.delete(fee)
    await db.commit()
    return None
