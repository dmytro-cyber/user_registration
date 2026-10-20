import httpx
import pytest

from services import apicar_ingestion


def test_streaming_batches_preserve_projection_and_partial_failure_counts(monkeypatch):
    requests = []

    def handle(request):
        assert request.url.host == "api.apicar.store"
        requests.append(request)
        page = int(request.url.params["page"])
        data = [
            {
                "vin": f"VIN{i}",
                "title": "Toyota",
                "make": "Toyota",
                "model": "Camry",
                "auction_date": "2026-01-02T00:00:00Z",
                "link_img_small": ["photo"],
            }
            for i in range(51)
        ]
        return httpx.Response(200, json={"data": [*data, {}] if page == 1 else []})

    real_client = httpx.Client
    monkeypatch.setattr(
        apicar_ingestion.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handle),
            **kwargs,
        ),
    )
    batches = []

    def save(batch):
        batches.append(batch)
        if len(batches) == 1:
            raise ValueError("First batch failed")

    result = apicar_ingestion.fetch_api_data(save)
    assert result == {"message": "Streaming finished", "pages": 1, "vehicles": 51}
    assert [len(batch["vehicles"]) for batch in batches] == [50, 1]
    assert all(batch["ivent"] == "updated" for batch in batches)
    assert batches[0]["vehicles"][0]["date"] == "2026-01-02T00:00:00+00:00"
    assert batches[0]["vehicles"][0]["photos"] == [{"url": "photo"}]
    assert len(requests) == 2


def test_initial_load_uses_created_event(monkeypatch):
    real_client = httpx.Client

    def handle(request):
        data = [{"vin": "VIN", "title": "Toyota", "make": "Toyota", "model": "Camry"}]
        return httpx.Response(200, json={"data": data if request.url.params["page"] == "1" else []})

    monkeypatch.setattr(
        apicar_ingestion.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handle),
            **kwargs,
        ),
    )
    batches = []
    apicar_ingestion.fetch_api_data(batches.append, size=5000)
    assert batches[0]["ivent"] == "created"


def test_deletion_batches_remain_bounded(monkeypatch):
    monkeypatch.setattr(
        apicar_ingestion.httpx,
        "get",
        lambda url, **kwargs: httpx.Response(
            200,
            json={"data": [{"lot_id": i, "site": 1} for i in range(201)]},
            request=httpx.Request("GET", url),
        ),
    )
    batches = []
    assert apicar_ingestion.delete_vehicle(batches.append) is None
    assert [len(batch["data"]) for batch in batches] == [100, 100, 1]


def test_external_deletion_errors_still_fail_task(monkeypatch):
    monkeypatch.setattr(
        apicar_ingestion.httpx,
        "get",
        lambda url, **kwargs: httpx.Response(
            503,
            request=httpx.Request("GET", url),
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        apicar_ingestion.delete_vehicle(lambda _: pytest.fail("Must not apply an unsuccessful fetch"))
