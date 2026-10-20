"""Source boundary and relevance policy for expired-auction refresh."""

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from schemas.vehicle import CarCreateSchema


class ExpiredVehicleSource(Protocol):
    def fetch(self, vin: str) -> "CarCreateSchema | None": ...


def should_archive_expired_vehicle(auction_name: str | None, date: datetime | None, now: datetime) -> bool:
    is_buynow = auction_name is not None and auction_name.lower() == "buynow"
    return not is_buynow and (date is None or date <= now)
