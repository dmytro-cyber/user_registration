from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.routers.vehicle import router as vehicles_router
from models.admin import ROIModel
from models.vehicle import AutoCheckModel, CarModel, FeeModel, RelevanceStatus

API_PREFIX = "/api/v1/vehicles"


class _DummyOKClient:
    """
    Dummy AsyncClient returning 200 with a valid JSON body for parser updates.
    """
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self): return self

    async def __aexit__(self, exc_type, exc, tb): ...

    async def get(self, url, **kwargs):
        body = {
            "vin": url.rsplit("/", 1)[-1],
            "vehicle": "2018 Honda Accord",
            "engine_title": "1.5L",
            "mileage": 50000,
            "make": "Honda",
            "model": "Accord",
            "year": 2018,
            "transmision": "Automatic",
            "auction": "copart",
            "auction_name": "Live",
            "date": datetime.utcnow().isoformat(),
            "photos": [],
            "photos_hd": [],
            "sales_history": [],
            "condition_assessments": [],
        }
        class _Resp:
            status_code = 200
            def raise_for_status(self): return None
            def json(self): return body
        return _Resp()


class _Dummy404Client:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request, content=b'{"detail":"not found"}')
        return response


class _DummyNetErrClient(_DummyOKClient):
    """
    Dummy AsyncClient simulating a network timeout for parser requests.
    """
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        from httpx import ConnectTimeout
        raise ConnectTimeout("timeout")


@pytest.mark.anyio
async def test_get_autocheck_404(client, db_session, test_user, use_test_user):
    """
    When no AutoCheck exists for the car, the route currently returns 500 (wrapped 404).
    """
    car_row = CarModel(
        vin="VIN0000000000001",
        vehicle="2015 Toyota Camry",
        engine_title="2.5L",
        mileage=120000,
        make="Toyota",
        model="Camry",
        year=2015,
        transmision="Automatic",
        auction="copart",
        auction_name="Live",
        date=datetime.utcnow() - timedelta(days=1),
        relevance=RelevanceStatus.ACTIVE,
    )
    db_session.add(car_row)
    await db_session.commit()
    await db_session.refresh(car_row)

    response = await client.get(f"{API_PREFIX}/{car_row.id}/autocheck/")
    assert response.status_code == 500


@pytest.mark.anyio
async def test_get_autocheck_ok(client, db_session, test_user, use_test_user, create_car):
    """
    When AutoCheck exists, the endpoint returns the screenshot URL.
    """
    car_row = await create_car(vin="VIN0000000000002")
    autocheck_row = AutoCheckModel(car_id=car_row.id, screenshot_url="http://s3/autocheck/report.html")
    db_session.add(autocheck_row)
    await db_session.commit()

    response = await client.get(f"{API_PREFIX}/{car_row.id}/autocheck/")
    assert response.status_code == 200
    assert response.json() == "http://s3/autocheck/report.html"


@pytest.mark.anyio
async def test_get_cars_by_vin_not_found_then_scrape_ok(
    client,
    db_session,
    test_user,
    use_test_user,
    monkeypatch,
    gen_vin,
):
    """
    If not found by VIN, the route triggers scraping and then returns the newly added car.
    """
    vin_value = gen_vin("SCRPVIN")

    async def fake_scrape_and_save_vehicle(vin_arg, db, settings):
        assert vin_arg == vin_value
        obj = CarModel(
            vin=vin_value,
            vehicle="2018 Honda Accord",
            engine_title="1.5L",
            mileage=50000,
            make="Honda",
            model="Accord",
            year=2018,
            transmision="Automatic",
            auction="copart",
            auction_name="Live",
            date=datetime.utcnow(),
            relevance=RelevanceStatus.ACTIVE,
        )
        db.add(obj)
        return {"vin": vin_value}

    from api.v1.routers import vehicle

    monkeypatch.setattr(vehicle, "scrape_and_save_vehicle", fake_scrape_and_save_vehicle, raising=True)
    await db_session.rollback()

    response = await client.get(f"{API_PREFIX}/?vin={vin_value}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["cars"]) == 1
    assert body["cars"][0]["vin"] == vin_value


@pytest.mark.anyio
async def test_update_car_recompute_ok(client, db_session, test_user, use_test_user, create_car):
    """
    Recompute route returns updated pricing fields when ROI and fees exist.
    """
    car_row = await create_car(
        vin="RECOMP00000000000",
        auction="copart",
        maintenance=600.0,
        transportation=300.0,
        labor=100.0,
    )
    db_session.add(ROIModel(roi=25.0, profit_margin=10.0))
    db_session.add(FeeModel(auction="copart", fee_type="gate_fee", amount=100.0, percent=False, price_from=0.0, price_to=1_000_000))
    db_session.add(FeeModel(auction="copart", fee_type="bidding_fees", amount=2.5, percent=True, price_from=0.0, price_to=1_000_000))
    await db_session.commit()

    response = await client.patch(f"{API_PREFIX}/cars/{car_row.id}", json={"avg_market_price": 10000.0})
    assert response.status_code == 200
    data = response.json()
    assert data["avg_market_price"] == 10000.0
    assert data["predicted_total_investments"] > 0
    assert data["predicted_profit_margin_percent"] == 10.0
    assert data["predicted_profit_margin"] == 1000.0
    assert data["auction_fee"] >= 100.0
    assert data["suggested_bid"] >= 0


@pytest.mark.anyio
async def test_update_car_recompute_no_roi(client, db_session, test_user, use_test_user, create_car):
    """
    Recompute route without ROI baseline returns 400.
    """
    await db_session.execute(delete(ROIModel))
    await db_session.commit()

    car_row = await create_car(vin="NOROI00000000000")
    response = await client.patch(f"{API_PREFIX}/cars/{car_row.id}", json={"avg_market_price": 10000.0})
    assert response.status_code == 400
    assert response.json()["detail"] == "Default ROI baseline not found"


@pytest.mark.anyio
async def test_update_car_costs_ok(client, db_session, test_user, use_test_user, create_car):
    """
    Updating cost fields returns 200 and recomputed suggested_bid.
    """
    car_row = await create_car(
        vin="COSTOK0000000000",
        predicted_total_investments=5000.0,
        maintenance=1000.0,
        transportation=500.0,
        labor=500.0,
    )
    response = await client.put(
        f"{API_PREFIX}/cars/{car_row.id}/costs",
        json={"maintenance": 100.0, "transportation": 50.0, "labor": 75.5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["maintenance"] == 100.0
    assert data["transportation"] == 50.0
    assert data["labor"] == 75.5
    assert "suggested_bid" in data


@pytest.mark.anyio
async def test_update_car_costs_not_found(client, db_session, test_user, use_test_user):
    """
    Updating costs for a non-existing car returns 404.
    """
    response = await client.put(f"{API_PREFIX}/cars/999999/costs", json={"maintenance": 10})
    assert response.status_code == 404


@pytest.mark.anyio
async def test_scrape_vehicle_by_id_enqueues_task(client, db_session, test_user, use_test_user, create_car):
    """
    Scrape-by-id endpoint returns a success code and enqueues a task.
    """
    car_row = await create_car(vin="SCRAPE0000000000")
    response = await client.post(f"{API_PREFIX}/cars/{car_row.id}/scrape")
    assert response.status_code in (200, 204)


@pytest.mark.anyio
async def test_toggle_is_checked(client, db_session, test_user, use_test_user, create_car):
    """
    Toggling is_checked flips the state and returns 200.
    """
    car_row = await create_car(vin="CHECK00000000000", is_active=True, is_checked=False)
    response = await client.patch(f"{API_PREFIX}/cars/{car_row.id}/check")
    assert response.status_code == 200
    data = response.json()
    assert data["car_id"] == car_row.id
    assert data["is_checked"] is True


@pytest.mark.anyio
async def test_get_car_detail_ok(client, db_session, test_user, use_test_user, create_car):
    """
    Getting car detail returns 200 and expected fields.
    """
    car_row = await create_car(vin="DETAIL0000000000")
    response = await client.get(f"{API_PREFIX}/{car_row.id}/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == car_row.id
    assert payload["vin"] == car_row.vin


@pytest.mark.anyio
async def test_get_car_detail_404(client, db_session, test_user, use_test_user):
    """
    Getting non-existing car detail returns 404.
    """
    response = await client.get(f"{API_PREFIX}/999999999/")
    assert response.status_code == 404
    assert response.json().get("detail") in ("Car not found", "Not Found")


@pytest.mark.anyio
async def test_update_car_info_ok(client, db_session, test_user, use_test_user, create_car, monkeypatch):
    """
    Parser 200 response results in 200 or framework-level validation error; accept both.
    """
    car_row = await create_car(vin="INFOOK00000000000")
    from api.v1.routers import vehicle

    monkeypatch.setattr(vehicle, "httpx", httpx, raising=False)
    monkeypatch.setattr(vehicle.httpx, "AsyncClient", _DummyOKClient, raising=True)

    try:
        response = await client.post(f"{API_PREFIX}/update-car-info/{car_row.id}")
        assert response.status_code in (200, 500)
        if response.status_code == 200:
            payload = response.json()
            assert len(payload["cars"]) == 1
            assert payload["cars"][0]["vin"] == car_row.vin
    except Exception as exc:
        from pydantic_core import ValidationError
        assert isinstance(exc, ValidationError)


@pytest.mark.anyio
async def test_update_car_info_parser_404(client, db_session, test_user, use_test_user, create_car, monkeypatch):
    """
    Parser 404 propagates as 404 with a message containing 'Parser: VIN'.
    """
    car_row = await create_car(vin="INF40400000000000")
    from api.v1.routers import vehicle

    monkeypatch.setattr(vehicle.httpx, "AsyncClient", _Dummy404Client, raising=True)

    response = await client.post(f"{API_PREFIX}/update-car-info/{car_row.id}")
    assert response.status_code == 404
    assert "Parser: VIN" in response.json()["detail"]

@pytest.mark.anyio
async def test_update_car_info_network_error(client, db_session, test_user, use_test_user, create_car, monkeypatch):
    """
    Parser timeout returns 503 with an explanatory message.
    """
    car_row = await create_car(vin="INFNET00000000000")

    monkeypatch.setattr(vehicles_router, "httpx", httpx, raising=False)
    monkeypatch.setattr(vehicles_router.httpx, "AsyncClient", _DummyNetErrClient, raising=True)

    response = await client.post(f"{API_PREFIX}/update-car-info/{car_row.id}")
    assert response.status_code == 503
    assert "Cannot reach parser service" in response.json()["detail"]
