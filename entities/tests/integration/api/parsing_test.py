import json
from pathlib import Path

import httpx
import pytest

from integrations import scraping_rpc
from integrations.apicar import client as apicar
from main import app

pytestmark = pytest.mark.anyio


def test_transferred_openapi_operations_are_unchanged():
    baseline = json.loads((Path(__file__).parents[2] / "fixtures/contracts/parsers-openapi.json").read_text())
    current = app.openapi()
    for path, operation in baseline["paths"].items():
        assert current["paths"][path] == operation, path
    for name, schema in baseline["components"]["schemas"].items():
        assert current["components"]["schemas"][name] == schema, name


async def test_apicar_history_projection_and_errors(client, monkeypatch):
    async def get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "data": {
                    "vin": "VIN",
                    "title": "Toyota",
                    "auction_date": "2026-01-02T12:00:00Z",
                    "link_img_small": ["photo"],
                    "keys": "Yes",
                    "engine_size": "2.5",
                }
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    # Patch the external client's method only while invoking the ASGI client via request.
    response = await client.request("GET", "/api/v1/apicar/get/VIN")
    assert response.status_code == 200
    assert response.json()["date"] == "2026-01-02T12:00:00+00:00"
    assert response.json()["photos"] == [{"url": "photo"}]
    assert response.json()["has_keys"] is True
    assert response.json()["engine"] == 2.5

    async def unavailable(self, url, **kwargs):
        raise httpx.ConnectError("unavailable", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", unavailable)
    response = await client.request("GET", "/api/v1/apicar/get/VIN")
    assert response.status_code == 200
    assert response.json() is None
    response = await client.request("GET", "/api/v1/apicar/update/VIN")
    assert response.status_code == 404
    assert response.json() == {"detail": "VIN VIN not found"}


async def test_dealercenter_route_uses_worker_and_keeps_response_defaults(client, monkeypatch):
    calls = []

    def execute(operation, payload, **kwargs):
        calls.append((operation, payload))
        return {"status": 200, "body": {"owners": 2}}

    monkeypatch.setattr(scraping_rpc, "execute", execute)
    response = await client.get(
        "/api/v1/parsers/scrape/dc",
        params={
            "car_vin": "VIN",
            "car_make": "Toyota",
            "car_model": "Camry",
            "car_year": 2018,
            "car_transmison": "Automatic",
            "only_history": True,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "owners": 2,
        "mileage": None,
        "accident_count": None,
        "jd": None,
        "manheim": None,
        "d_max": None,
        "html_data": None,
        "error": None,
    }
    assert calls[0][0] == "dealercenter"
    assert calls[0][1]["only_history"] is True
    assert calls[0][1]["car_year"] == 2018


async def test_dealercenter_invalid_year_does_not_dispatch(client, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid input must not be dispatched")

    monkeypatch.setattr(scraping_rpc, "execute", unexpected)
    response = await client.get(
        "/api/v1/parsers/scrape/dc",
        params={
            "car_vin": "VIN",
            "car_make": "Toyota",
            "car_model": "Camry",
            "car_year": "bad",
            "car_transmison": "Automatic",
        },
    )
    assert response.status_code == 422


async def test_current_bid_batching_and_failure_policy(client, monkeypatch):
    requests = []

    async def post(self, url, **kwargs):
        requests.append(kwargs["json"])
        if len(requests) == 1:
            return httpx.Response(503, request=httpx.Request("POST", url))
        return httpx.Response(200, json=[{"lot_id": 20, "pre_bid": 100}], request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    # request() avoids replacing the ASGI client's own post method.
    response = await client.request(
        "POST",
        "/api/v1/parsers/scrape/current_bid",
        json={"items": [{"id": i, "lot": i, "source": "Copart" if i % 2 == 0 else "IAAI"} for i in range(21)]},
    )
    assert response.status_code == 200
    assert response.json() == {"bids": [{"lot_id": 20, "pre_bid": 100}]}
    assert [len(item["lots"]) for item in requests] == [20, 1]
    assert requests[0]["lots"][:2] == [{"lot_id": 0, "site": 1}, {"lot_id": 1, "site": 2}]


async def test_missing_legacy_apicar_path_stays_404(client):
    assert (await client.get("/api/v1/apicar/VIN")).status_code == 404


async def test_startup_uses_legacy_task_name_and_args(client, monkeypatch):
    from core.integration_celery import app as integration_app

    calls = []
    monkeypatch.setattr(integration_app, "send_task", lambda *args, **kwargs: calls.append((args, kwargs)))
    response = await client.post("/startup")
    assert response.status_code == 200
    assert response.json() is None
    assert calls == [
        (
            ("tasks.tasks.fetch_api_data",),
            {
                "kwargs": {
                    "base_url": "https://api.apicar.store/api/cars/db/all",
                    "size": 5000,
                }
            },
        )
    ]


async def test_invalid_fee_upload_is_rejected_before_worker_dispatch(client, monkeypatch):
    monkeypatch.setattr(scraping_rpc, "execute", lambda *args, **kwargs: pytest.fail("Must reject before dispatch"))
    response = await client.post(
        "/api/v1/parsers/scrape/iaai/fees",
        files={
            "high_volume": ("bad.txt", b"invalid"),
            "internet_bid": ("fees.png", b"image"),
        },
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "File bad.txt must be .png or .svg"}
