"""Synchronous queries for bid refresh; the service owns the transaction."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from domain.bid_refresh import BidTarget
from models.vehicle import CarModel, RelevanceStatus


def load_bid_targets(db: Session) -> list[BidTarget]:
    rows = db.execute(
        select(CarModel.id, CarModel.lot, CarModel.auction).where(
            and_(
                CarModel.relevance == RelevanceStatus.ACTIVE,
                CarModel.lot.isnot(None),
                CarModel.auction.isnot(None),
                or_(CarModel.date.isnot(None), func.lower(CarModel.auction_name) == "buynow"),
            )
        )
    ).all()
    return [BidTarget(row.id, row.lot, row.auction) for row in rows]


def find_vehicle_for_bid_update(db: Session, lot: int, auction: str) -> CarModel | None:
    return (
        db.execute(
            select(CarModel)
            .where(and_(CarModel.lot == lot, func.lower(func.coalesce(CarModel.auction, "")) == auction))
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .first()
    )
