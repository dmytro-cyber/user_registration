"""Read-only ROI and fee selection for enrichment projections."""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.admin import ROIModel
from models.vehicle import FeeModel


def load_default_roi(db: Session) -> Optional[ROIModel]:
    return db.execute(select(ROIModel).order_by(ROIModel.created_at.desc()).limit(1)).scalars().first()


def load_enrichment_fees(db: Session, auction: Optional[str], investment: float) -> List[FeeModel]:
    return (
        db.execute(
            select(FeeModel).where(
                FeeModel.auction == auction,
                FeeModel.price_from <= investment,
                FeeModel.price_to >= investment,
            )
        )
        .scalars()
        .all()
    )
