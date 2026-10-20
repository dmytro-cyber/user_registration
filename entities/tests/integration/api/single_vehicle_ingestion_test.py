from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from integrations.apicar import client as apicar
from integrations.parsing import ParsingClient
from models.vehicle import CarModel, PhotoModel
from schemas.vehicle import CarCreateSchema

pytestmark = pytest.mark.asyncio
PREFIX = "/api/v1/vehicles/"


async def legacy_error(vin):
    async with ParsingClient() as client:
        try:
            response = await client.get(f"/api/v1/apicar/get/{vin}")
            response.raise_for_status()
            CarCreateSchema.model_validate(response.json())
        except httpx.HTTPError as exc:
            return 503, {"detail": f"Failed to fetch data from parser: {exc}"}
        except Exception as exc:
            return 422, {"detail": f"Invalid data from parser: {exc}"}
    pytest.fail("Expected a legacy error")


@pytest.mark.parametrize("payload", [None, {}, {"vin": "INVALID", "year": "bad year"}])
async def test_source_validation_keeps_exact_legacy_error(
    client,
    db_session,
    use_test_user,
    gen_vin,
    monkeypatch,
    payload,
):
    vin = gen_vin("SOURCE")
    monkeypatch.setattr(apicar, "get_vehicle_async", AsyncMock(return_value=payload))
    expected_status, expected_body = await legacy_error(vin)
    response = await client.get(PREFIX, params={"vin": vin})
    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert (await db_session.execute(select(CarModel))).scalars().all() == []


@pytest.mark.parametrize("failure", [ValueError("bad upstream payload"), apicar.ApicarVehicleNotFound("VIN")])
async def test_conversion_failure_keeps_exact_legacy_error(
    client,
    db_session,
    use_test_user,
    gen_vin,
    monkeypatch,
    failure,
):
    vin = gen_vin("CONVERT")
    monkeypatch.setattr(apicar, "get_vehicle_async", AsyncMock(side_effect=failure))
    expected_status, expected_body = await legacy_error(vin)
    response = await client.get(PREFIX, params={"vin": vin})
    assert response.status_code == expected_status == 503
    assert response.json() == expected_body


async def test_single_vin_ingests_photos_without_requiring_enrichment(
    client,
    db_session,
    use_test_user,
    gen_vin,
    monkeypatch,
):
    vin = gen_vin("INGEST")
    fetch = AsyncMock(
        return_value={
            "vin": vin,
            "vehicle": "2018 Toyota Camry",
            "year": 2018,
            "mileage": 50000,
            "auction": "copart",
            "transmision": "Automatic",
            "fuel_type": "Gasoline",
            "photos": [{"url": "https://photos.test/car.jpg"}],
        }
    )
    monkeypatch.setattr(apicar, "get_vehicle_async", fetch)
    response = await client.get(PREFIX, params={"vin": vin})
    assert response.status_code == 200
    assert len(response.json()["cars"]) == 1
    assert response.json()["cars"][0]["vin"] == vin
    assert response.json()["cars"][0]["photos"] == ["https://photos.test/car.jpg"]
    assert response.json()["last"] is True
    assert response.json()["page_links"] == {}
    assert (await db_session.execute(select(CarModel.vin))).scalar_one() == vin
    assert (await db_session.execute(select(PhotoModel.url))).scalar_one() == "https://photos.test/car.jpg"
    fetch.assert_awaited_once_with(vin)

    fetch.reset_mock()
    cached = await client.get(PREFIX, params={"vin": vin})
    assert cached.json() == response.json()
    fetch.assert_not_awaited()


async def test_unpersisted_source_snapshot_retains_500(
    client,
    db_session,
    use_test_user,
    gen_vin,
    monkeypatch,
):
    vin = gen_vin("NOTSAVED")
    # Snapshot service reports write errors; ingestion preserves its read-after-save policy.
    monkeypatch.setattr(apicar, "get_vehicle_async", AsyncMock(return_value={"vin": vin}))
    response = await client.get(PREFIX, params={"vin": vin})
    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to retrieve saved vehicle"}
    assert (await db_session.execute(select(CarModel))).scalars().all() == []
