from unittest.mock import AsyncMock

import httpx
import pytest

from exceptions.vehicle_source import InvalidVehicleSourceData, VehicleSourceUnavailable
from integrations.apicar import client as apicar
from integrations.apicar.vehicle_source import ApicarVehicleDataSource
from integrations.parsing import ParsingClient

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("failure", [404, 500, "timeout"])
async def test_upstream_http_failure_preserves_empty_payload_policy(monkeypatch, failure):
    def handle(request):
        assert request.url.host == "api.apicar.store"
        assert request.url.params["vin"] == "SOURCE-VIN"
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream timeout", request=request)
        return httpx.Response(failure)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        apicar.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(handle),
        ),
    )
    with pytest.raises(InvalidVehicleSourceData, match="Expecting value: line 1 column 1"):
        await ApicarVehicleDataSource().fetch("SOURCE-VIN")


@pytest.mark.parametrize("vin", ["BAD/VIN", "BAD%2FVIN"])
async def test_invalid_legacy_path_does_not_fetch_and_preserves_error(monkeypatch, vin):
    fetch = AsyncMock()
    monkeypatch.setattr(apicar, "get_vehicle_async", fetch)
    async with ParsingClient() as client:
        response = await client.get(f"/api/v1/apicar/get/{vin}")
    with pytest.raises(httpx.HTTPError) as legacy:
        response.raise_for_status()
    with pytest.raises(VehicleSourceUnavailable) as current:
        await ApicarVehicleDataSource().fetch(vin)
    assert str(current.value) == str(legacy.value)
    fetch.assert_not_awaited()
