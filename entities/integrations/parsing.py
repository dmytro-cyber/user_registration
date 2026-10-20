"""Local compatibility boundary for callers migrating from the parsers HTTP API.

No requests are made to the legacy host. APICAR calls execute locally; browser/OCR
operations use their dedicated queue. HTTPX responses retain existing error mapping
in callers until those callers migrate to typed integration results.
"""

import asyncio
import base64
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from celery.exceptions import TimeoutError as CeleryTimeoutError
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError

from core.integration_celery import app
from integrations import scraping_rpc
from integrations.apicar import client as apicar

LEGACY_ORIGIN = "http://parsers:8001"  # Error metadata only, never a network destination.


class InvalidFeeUpload(Exception):
    pass


class DealerCenterInput(BaseModel):
    car_vin: str
    car_name: str | None = None
    car_engine: str | None = None
    car_mileage: int | None = None
    car_make: str
    car_model: str
    car_year: int
    car_transmison: str
    only_history: bool = False


def _response(method, url, status=200, body=None):
    request = httpx.Request(method, url if "://" in url else LEGACY_ORIGIN + url)
    return httpx.Response(status, json=jsonable_encoder(body), request=request)


def _path(url):
    return urlsplit(url).path


def _apicar_route(url):
    path = _path(url)
    for prefix, update in (("/api/v1/apicar/get/", False), ("/api/v1/apicar/update/", True)):
        if path.startswith(prefix):
            vin = unquote(path[len(prefix) :])
            if vin and "/" not in vin:
                return vin, update
    return None


def _dealercenter_payload(url):
    query = {key: values[-1] for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
    return DealerCenterInput.model_validate(query).model_dump()


def _error(method, url, exc):
    if isinstance(exc, InvalidFeeUpload):
        return _response(method, url, 400, {"detail": str(exc)})
    if isinstance(exc, apicar.ApicarVehicleNotFound):
        return _response(method, url, 404, {"detail": f"VIN {exc.args[0]} not found"})
    if isinstance(exc, ValidationError):
        detail = [{**error, "loc": ["query", *error["loc"]]} for error in exc.errors(include_url=False)]
        return _response(method, url, 422, {"detail": detail})
    request = httpx.Request(method, url if "://" in url else LEGACY_ORIGIN + url)
    if isinstance(exc, CeleryTimeoutError):
        raise httpx.ReadTimeout("Timed out waiting for scraping worker", request=request) from exc
    return httpx.Response(500, text="Internal Server Error", request=request)


def _scraping_operation(url, files=None):
    if _path(url) == "/api/v1/parsers/scrape/dc":
        return "dealercenter", _dealercenter_payload(url)
    if _path(url) == "/api/v1/parsers/scrape/fees":
        return "copart_fees", {}
    if _path(url) == "/api/v1/parsers/scrape/iaai/fees" and files is not None:
        for filename, *_ in files.values():
            if Path(filename.lower()).suffix not in {".png", ".svg"}:
                raise InvalidFeeUpload(f"File {filename} must be .png or .svg")
        return "iaai_fees", {
            name: {"filename": value[0], "content": base64.b64encode(value[1]).decode("ascii")}
            for name, value in files.items()
        }
    return None


def start_import():
    app.send_task(
        "tasks.tasks.fetch_api_data",
        kwargs={
            "base_url": "https://api.apicar.store/api/cars/db/all",
            "size": 5000,
        },
    )


class ParsingClient:
    def __init__(self, timeout=60, **_):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, **kwargs):
        try:
            route = _apicar_route(url)
            if route:
                body = await apicar.get_vehicle_async(route[0], update=route[1])
                return _response("GET", url, body=body)
            operation = _scraping_operation(url)
            if operation:
                result = await asyncio.to_thread(scraping_rpc.execute, *operation, timeout=self.timeout)
                return _response("GET", url, result["status"], result["body"])
            return _response("GET", url, 404, {"detail": "Not Found"})
        except Exception as exc:
            return _error("GET", url, exc)

    async def post(self, url, *, json=None, files=None, **kwargs):
        try:
            if _path(url) == "/startup":
                start_import()
                return _response("POST", url)
            if _path(url) == "/api/v1/parsers/scrape/current_bid":
                return _response("POST", url, body=await apicar.current_bids_async(json["items"]))
            operation = _scraping_operation(url, files)
            if operation:
                result = await asyncio.to_thread(scraping_rpc.execute, *operation, timeout=self.timeout)
                return _response("POST", url, result["status"], result["body"])
            return _response("POST", url, 404, {"detail": "Not Found"})
        except Exception as exc:
            return _error("POST", url, exc)


class SyncParsingClient:
    def __init__(self, timeout=60, **_):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, **kwargs):
        try:
            route = _apicar_route(url)
            if route:
                return _response("GET", url, body=apicar.get_vehicle(route[0], update=route[1]))
            operation = _scraping_operation(url)
            if operation:
                result = scraping_rpc.execute(*operation, timeout=self.timeout)
                return _response("GET", url, result["status"], result["body"])
            return _response("GET", url, 404, {"detail": "Not Found"})
        except Exception as exc:
            return _error("GET", url, exc)

    def post(self, url, *, json=None, **kwargs):
        try:
            if _path(url) == "/api/v1/parsers/scrape/current_bid":
                return _response("POST", url, body=apicar.current_bids(json["items"]))
            return _response("POST", url, 404, {"detail": "Not Found"})
        except Exception as exc:
            return _error("POST", url, exc)
