"""Preserve the existing expired-auction parser path and failure boundaries."""

from collections.abc import Callable
from typing import Any

import httpx

from exceptions.expired_vehicles import ExpiredVehicleSourceError
from schemas.vehicle import CarCreateSchema


class ParsingExpiredVehicleSource:
    def __init__(self, client_factory: Callable[..., Any]) -> None:
        self._client_factory = client_factory

    def fetch(self, vin: str) -> CarCreateSchema | None:
        try:
            response = self._client_factory(timeout=30).get(f"/api/v1/apicar/{vin}", timeout=30.0)
        except Exception as exc:
            raise ExpiredVehicleSourceError("HTTP request failed", exc) from exc
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ExpiredVehicleSourceError("parser returned HTTP error", exc) from exc
        try:
            return CarCreateSchema.model_validate(response.json())
        except Exception as exc:
            raise ExpiredVehicleSourceError("payload validation failed", exc) from exc
