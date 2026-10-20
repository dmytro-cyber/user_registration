"""Boundary for a validated vehicle snapshot supplied by an integration."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from schemas.vehicle import CarCreateSchema


class VehicleDataSource(Protocol):
    async def fetch(self, vin: str) -> "CarCreateSchema": ...
