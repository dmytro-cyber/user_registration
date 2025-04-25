from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from datetime import datetime

from models.filter import FilterModel
from schemas.filter import FilterCreate, FilterUpdate, FilterResponse, FilterUpdateTimestamp
from db.session import get_db
from core.dependencies import get_token, get_current_user


router = APIRouter(prefix="/filters")


# Create a new filter
@router.post("/", response_model=FilterResponse, status_code=status.HTTP_201_CREATED)
async def create_filter(
    filter: FilterCreate, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    db_filter = FilterModel(**filter.dict(exclude_unset=True))
    db_filter.updated_at = datetime.utcnow()
    db.add(db_filter)
    await db.commit()
    await db.refresh(db_filter)
    return db_filter


# Get all filters
@router.get("/", response_model=List[FilterResponse])
async def get_filters(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FilterModel).offset(skip).limit(limit))
    filters = result.scalars().all()
    return filters


# Get a single filter by ID
@router.get("/{filter_id}", response_model=FilterResponse)
async def get_filter(filter_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
    filter = result.scalars().first()
    if not filter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
    return filter


# Update a filter (partial update)
@router.patch("/{filter_id}", response_model=FilterResponse)
async def update_filter(
    filter_id: int,
    filter_update: FilterUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
    db_filter = result.scalars().first()
    if not db_filter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

    update_data = filter_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        if value:
            setattr(db_filter, key, value)

    db_filter.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(db_filter)
    return db_filter


# # Update only the updated_at field of a filter
# @router.patch("/{filter_id}/timestamp", response_model=FilterResponse)
# async def update_filter_timestamp(filter_id: int, timestamp_update: FilterUpdateTimestamp, db: AsyncSession = Depends(get_db)):
#     result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
#     db_filter = result.scalars().first()
#     if not db_filter:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

#     db_filter.updated_at = timestamp_update.updated_at
#     await db.commit()
#     await db.refresh(db_filter)
#     return db_filter


@router.patch("/{filter_id}/timestamp")
async def update_filter_timestamp(
    filter_id: int, update_data: FilterUpdateTimestamp, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
    db_filter = result.scalars().first()
    if not db_filter:
        raise HTTPException(status_code=404, detail="Filter not found")

    # Видаляємо часовий пояс, якщо він є
    updated_at_naive = update_data.updated_at.replace(tzinfo=None)
    db_filter.updated_at = updated_at_naive
    await db.commit()
    await db.refresh(db_filter)
    return db_filter


# Delete a filter
@router.delete("/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(filter_id: int, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FilterModel).filter(FilterModel.id == filter_id))
    db_filter = result.scalars().first()
    if not db_filter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

    await db.delete(db_filter)
    await db.commit()
    return
