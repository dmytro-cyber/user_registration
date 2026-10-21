"""Admin ROI use cases with the existing read and transaction policies."""

from sqlalchemy.ext.asyncio import AsyncSession

from crud.roi import add_roi_record, latest_roi_record, list_roi_records
from domain.roi import normalize_admin_roi
from exceptions.roi import ROIRecordNotFound
from models.admin import ROIModel


async def create_admin_roi(db: AsyncSession, value: float) -> ROIModel:
    normalized = normalize_admin_roi(value)
    try:
        record = add_roi_record(db, normalized)
        await db.commit()
        await db.refresh(record)
        return record
    except Exception:
        await db.rollback()
        raise


async def list_admin_roi(db: AsyncSession) -> list[ROIModel]:
    records = await list_roi_records(db)
    if not records:
        raise ROIRecordNotFound()
    return records


async def latest_admin_roi(db: AsyncSession) -> ROIModel:
    record = await latest_roi_record(db)
    if record is None:
        raise ROIRecordNotFound()
    return record
