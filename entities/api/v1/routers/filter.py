from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from datetime import datetime

from models.filter import FilterModel
from schemas.filter import FilterCreate, FilterUpdate, FilterResponse
from db.session import get_db

router = APIRouter(prefix="/filters", tags=["filters"])



@router.post("/", response_model=FilterResponse, status_code=status.HTTP_201_CREATED)
async def create_filter(filter: FilterCreate, db: AsyncSession = Depends(get_db)):
    db_filter = FilterModel(**filter.dict(exclude_unset=True))
    db_filter.updated_at = datetime.utcnow()
    db.add(db_filter)
    await db.commit()
    await db.refresh(db_filter)
    return db_filter


@router.get("/", response_model=List[FilterResponse])
async def get_filters(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FilterModel).offset(skip).limit(limit))
    filters = result.scalars().all()
    return filters


@router.get("/{filter_id}", response_model=FilterResponse)
async def get_filter(filter_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
    filter = result.scalars().first()
    if not filter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
    return filter


@router.put("/{filter_id}", response_model=FilterResponse)
async def update_filter(filter_id: int, filter_update: FilterUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
    db_filter = result.scalars().first()
    if not db_filter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
    
    update_data = filter_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_filter, key, value)
    
    db_filter.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(db_filter)
    return db_filter


@router.delete("/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(filter_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
    db_filter = result.scalars().first()
    if not db_filter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
    
    await db.delete(db_filter)
    await db.commit()
    return