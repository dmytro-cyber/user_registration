"""Filter execution queries; transaction boundaries belong to the service."""

from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from models.admin import FilterModel
from models.filter_kickoff_queue import FilterKickoffQueueModel, FilterKickoffQueueStatus
from models.vehicle import CarModel


def load_filter_vehicles(session: Session, filter_id: int) -> list[RowMapping] | None:
    filt = session.get(FilterModel, filter_id)
    if filt is None:
        return None
    conditions = [
        CarModel.make == filt.make,
        CarModel.year >= (filt.year_from or 0),
        CarModel.year <= (filt.year_to or 3000),
        CarModel.mileage >= (filt.odometer_min or 0),
        CarModel.mileage <= (filt.odometer_max or 10_000_000),
        CarModel.avg_market_price.is_(None),
        CarModel.is_checked.is_(False),
        CarModel.attempts < 3,
    ]

    if filt.model is not None:
        conditions.append(CarModel.model == filt.model)

    stmt = select(
        CarModel.vin,
        CarModel.vehicle,
        CarModel.engine_title,
        CarModel.mileage,
        CarModel.make,
        CarModel.model,
        CarModel.year,
        CarModel.transmision,
    ).where(and_(*conditions))

    return list(session.execute(stmt).mappings().all())


def finish_filter_kickoff(
    session: Session, queue_item_id: int, status: FilterKickoffQueueStatus, finished_at: datetime, error: str | None
) -> bool:
    item = session.get(FilterKickoffQueueModel, queue_item_id)
    if item is None:
        return False
    item.status = status
    item.finished_at = finished_at
    item.last_error = error
    return True
