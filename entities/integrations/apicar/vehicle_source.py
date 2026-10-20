"""Typed APICAR source, preserving the former parser error contract."""

import json
from urllib.parse import unquote, urlsplit

import httpx
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from exceptions.vehicle_source import InvalidVehicleSourceData, VehicleSourceUnavailable
from integrations.apicar import client as apicar
from schemas.vehicle import CarCreateSchema


def _legacy_failure(vin: str, status: int, reason: str) -> VehicleSourceUnavailable:
    # This address is diagnostic text retained in public errors, never a destination.
    url = str(httpx.URL(f"http://parsers:8001/api/v1/apicar/get/{vin}"))
    category = "Client" if status < 500 else "Server"
    return VehicleSourceUnavailable(
        f"{category} error '{status} {reason}' for url '{url}'\n"
        f"For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{status}"
    )


class ApicarVehicleDataSource:
    async def fetch(self, vin: str) -> CarCreateSchema:
        # Preserve the old path dispatch for VINs containing URL delimiters.
        path = urlsplit(f"/api/v1/apicar/get/{vin}").path
        requested_vin = unquote(path[len("/api/v1/apicar/get/") :])
        if not requested_vin or "/" in requested_vin:
            raise _legacy_failure(vin, 404, "Not Found")
        try:
            payload = jsonable_encoder(await apicar.get_vehicle_async(requested_vin))
        except apicar.ApicarVehicleNotFound as exc:
            raise _legacy_failure(vin, 404, "Not Found") from exc
        except ValidationError as exc:
            raise _legacy_failure(vin, 422, "Unprocessable Entity") from exc
        except Exception as exc:
            raise _legacy_failure(vin, 500, "Internal Server Error") from exc
        try:
            if payload is None:
                # HTTPX's former json=None response had an empty body, not JSON null.
                json.loads("")
            return CarCreateSchema.model_validate(payload)
        except Exception as exc:
            raise InvalidVehicleSourceData(str(exc)) from exc
