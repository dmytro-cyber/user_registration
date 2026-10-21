from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_current_user
from db.session import get_db
from exceptions.fees import FeeNotFound
from models.user import UserModel
from schemas.vehicle import FeeCreate, FeeRead, FeeUpdate
from services.fees import create_manual_fee, delete_manual_fee, list_manual_fees, update_manual_fee

router = APIRouter(prefix="/fees")


@router.post("/", response_model=FeeRead, status_code=status.HTTP_201_CREATED)
async def create_fee(
    payload: FeeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    return await create_manual_fee(db, payload)


@router.get("/", response_model=List[FeeRead])
async def list_fees(
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    return await list_manual_fees(db)


@router.put("/{fee_id}", response_model=FeeRead)
async def update_fee(
    fee_id: int,
    payload: FeeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    try:
        return await update_manual_fee(db, fee_id, payload)
    except FeeNotFound:
        raise HTTPException(status_code=404, detail="Fee not found")


@router.delete("/{fee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fee(
    fee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user)
):
    try:
        await delete_manual_fee(db, fee_id)
    except FeeNotFound:
        raise HTTPException(status_code=404, detail="Fee not found")
    return None
