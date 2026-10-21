"""Admin ROI persistence; application services own commit/rollback."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import ROIModel


def add_roi_record(db: AsyncSession, value: float) -> ROIModel:
    record = ROIModel(roi=value)
    db.add(record)
    return record


async def list_roi_records(db: AsyncSession) -> list[ROIModel]:
    return (await db.execute(select(ROIModel).order_by(ROIModel.id.desc()))).scalars().all()


async def latest_roi_record(db: AsyncSession) -> ROIModel | None:
    return (await db.execute(select(ROIModel).order_by(ROIModel.id.desc()).limit(1))).scalars().first()
