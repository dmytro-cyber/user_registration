from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, select

from models.admin import FilterModel
from models.vehicle import (
    CarModel,
    CarStatus,
    ConditionAssessmentModel,
    PhotoModel,
    RecommendationStatus,
    RelevanceStatus,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_periodic_import_bridge_uses_bulk_service_directly(postgres_sessions, monkeypatch):
    from core.celery_config import app as celery_app
    from db import session as sessions
    from tasks.ingestion import save_batch

    monkeypatch.setattr(sessions, "SessionLocal", postgres_sessions)
    calls = []
    monkeypatch.setattr(celery_app, "send_task", lambda *args, **kwargs: calls.append((args, kwargs)))
    await seed_filter(postgres_sessions)
    await save_batch({"ivent": "updated", "vehicles": [vehicle("DIRECT-IMPORT")]})
    async with postgres_sessions() as db:
        assert (await db.execute(select(CarModel))).scalar_one().vin == "DIRECT-IMPORT"
    assert calls[0][0] == ("tasks.task.parse_and_update_car",)
    assert calls[0][1]["queue"] == "car_parsing_queue"


def vehicle(vin, **changes):
    return {
        "vin": vin,
        "vehicle": "2018 Toyota Camry",
        "engine_title": "2.5L",
        "make": "Toyota",
        "model": "Camry",
        "year": 2018,
        "mileage": 50000,
        "auction": "Copart",
        "fuel_type": "Gasoline",
        "transmision": "Automatic",
        **changes,
    }


@pytest.fixture
async def bulk_api(postgres_sessions, monkeypatch):
    from api.v1.routers import vehicle as router_module
    from db.session import get_db
    from main import app

    observer = create_engine(postgres_sessions.kw["bind"].url.set(drivername="postgresql+psycopg2"))
    publications = []

    def publish(name, *, kwargs, queue):
        # A separate connection can see only committed imports.
        with observer.connect() as connection:
            committed_vins = set(connection.execute(select(CarModel.vin)).scalars())
        publications.append((name, kwargs, queue, committed_vins))

    monkeypatch.setattr(router_module.celery_app, "send_task", publish)

    async def override_db():
        async with postgres_sessions() as db:
            try:
                yield db
            finally:
                if db.in_transaction():
                    await db.rollback()

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(client=client, publications=publications, publish=publish, router=router_module)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        observer.dispose()


async def post_batch(api, vehicles, ivent="updated"):
    return await api.client.post(
        "/api/v1/vehicles/bulk",
        json={"ivent": ivent, "vehicles": vehicles},
        headers={"X-Auth-Token": "test-parsers-token"},
    )


async def seed_filter(sessions):
    async with sessions() as db:
        # Bulk matching currently ignores odometer_min; preserve that behavior.
        db.add(
            FilterModel(
                make="Toyota", model=None, year_from=2015, year_to=2020, odometer_min=60000, odometer_max=100000
            )
        )
        await db.commit()


async def test_bulk_deduplicates_vins_and_child_photos(bulk_api, postgres_sessions):
    response = await post_batch(
        bulk_api,
        [
            vehicle("DUPLICATE", mileage=1),
            vehicle(
                "DUPLICATE",
                mileage=50001,
                photos=[{"url": "same"}, {"url": "same"}],
                photos_hd=[{"url": "same"}],
                condition_assessments=[{"type_of_damage": "primary", "issue_description": "Mechanical"}],
            ),
            vehicle(""),
        ],
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"message": "Cars processed", "new": 1, "updated": 0}
    assert bulk_api.publications == []
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert car.mileage == 50001
        assert car.relevance == RelevanceStatus.IRRELEVANT
        assert car.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
        assert car.recommendation_status_reasons == "Mechanical;"
        assert {(p.url, p.is_hd) for p in (await db.execute(select(PhotoModel))).scalars()} == {
            ("same", False),
            ("same", True),
        }
        assert (await db.execute(select(ConditionAssessmentModel))).scalar_one().issue_description == "Mechanical"


async def test_bulk_publishes_exact_enrichment_payload_after_commit(bulk_api, postgres_sessions):
    await seed_filter(postgres_sessions)
    response = await post_batch(bulk_api, [vehicle("MATCH"), vehicle("OTHER", make="Ford")])
    assert response.status_code == 201, response.text
    assert response.json() == {"message": "Cars processed", "new": 2, "updated": 0}
    assert bulk_api.publications == [
        (
            "tasks.task.parse_and_update_car",
            {
                "vin": "MATCH",
                "car_name": "2018 Toyota Camry",
                "car_engine": "2.5L",
                "mileage": 50000,
                "car_make": "Toyota",
                "car_model": "Camry",
                "car_year": 2018,
                "car_transmison": "Automatic",
            },
            "car_parsing_queue",
            {"MATCH", "OTHER"},
        )
    ]


@pytest.mark.parametrize("payload", [[], [vehicle("")]])
async def test_empty_effective_batch_is_successful(bulk_api, payload):
    response = await post_batch(bulk_api, payload)
    assert response.status_code == 201
    assert response.json() == {"message": "Cars processed", "new": 0, "updated": 0}
    assert bulk_api.publications == []


@pytest.mark.parametrize(
    "relevance,checked,attempts,ivent,should_publish,expected_relevance",
    [
        (RelevanceStatus.ACTIVE, False, 2, "updated", True, RelevanceStatus.ACTIVE),
        (RelevanceStatus.ACTIVE, True, 0, "updated", False, RelevanceStatus.ACTIVE),
        (RelevanceStatus.ACTIVE, False, 3, "updated", False, RelevanceStatus.ACTIVE),
        (RelevanceStatus.ARCHIVAL, True, 3, "update", True, RelevanceStatus.ACTIVE),
        (RelevanceStatus.ARCHIVAL, True, 3, "updated", False, RelevanceStatus.ARCHIVAL),
    ],
)
async def test_bulk_preserves_parse_lifecycle(
    bulk_api,
    postgres_sessions,
    relevance,
    checked,
    attempts,
    ivent,
    should_publish,
    expected_relevance,
):
    await seed_filter(postgres_sessions)
    async with postgres_sessions() as db:
        db.add(
            CarModel(vin="EXISTING", vehicle="Original", relevance=relevance, is_checked=checked, attempts=attempts)
        )
        await db.commit()
    response = await post_batch(bulk_api, [vehicle("EXISTING")], ivent)
    assert response.status_code == 201, response.text
    assert response.json() == {"message": "Cars processed", "new": 0, "updated": 1}
    assert bool(bulk_api.publications) is should_publish
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert car.relevance == expected_relevance
        # Parse-owned fields are protected even in the archival reactivation branch.
        assert car.is_checked is checked
        assert car.attempts == attempts


async def test_bulk_preserves_manual_edits_but_reconsiders_changed_bid(bulk_api, postgres_sessions):
    async with postgres_sessions() as db:
        car = CarModel(
            vin="MANUAL",
            vehicle="User title",
            is_manually_upserted=True,
            actual_bid=8000,
            current_bid=6000,
            parts_cost=500,
            car_status=CarStatus.WON,
            recommendation_status=RecommendationStatus.RECOMMENDED,
            recommendation_manually_set=True,
        )
        db.add(car)
        await db.flush()
        db.add(ConditionAssessmentModel(car_id=car.id, type_of_damage="primary", issue_description="User damage"))
        await db.commit()
    response = await post_batch(
        bulk_api,
        [
            vehicle(
                "MANUAL",
                current_bid=9000,
                parts_cost=0,
                condition_assessments=[{"type_of_damage": "primary", "issue_description": "Mechanical"}],
                photos=[{"url": "ignored-photo"}],
            )
        ],
    )
    assert response.status_code == 201, response.text
    assert bulk_api.publications == []
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert (car.vehicle, car.actual_bid, car.parts_cost, car.car_status) == (
            "User title",
            8000,
            500,
            CarStatus.WON,
        )
        assert car.current_bid == 9000
        assert car.recommendation_manually_set is False
        assert car.recommendation_status_reasons == "BID_GT_SUGGESTED;"
        assert (await db.execute(select(ConditionAssessmentModel))).scalar_one().issue_description == "User damage"
        assert not (await db.execute(select(PhotoModel))).scalars().all()


async def test_bulk_child_write_failure_rolls_back_parent_and_children(bulk_api, postgres_sessions):
    async with postgres_sessions() as db:
        car = CarModel(vin="ATOMIC", vehicle="Original", relevance=RelevanceStatus.ACTIVE)
        db.add(car)
        await db.flush()
        db.add(ConditionAssessmentModel(car_id=car.id, type_of_damage="primary", issue_description="Original damage"))
        await db.commit()

    def fail_photo_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO photos"):
            raise RuntimeError("photo write failed")

    engine = postgres_sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", fail_photo_insert)
    try:
        response = await post_batch(
            bulk_api,
            [
                vehicle(
                    "ATOMIC",
                    photos=[{"url": "new-photo"}],
                    condition_assessments=[{"type_of_damage": "primary", "issue_description": "Mechanical"}],
                )
            ],
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_photo_insert)
    assert response.status_code == 500
    assert response.json() == {"detail": "Bulk error"}
    assert bulk_api.publications == []
    async with postgres_sessions() as db:
        assert (await db.execute(select(CarModel))).scalar_one().vehicle == "Original"
        assert (await db.execute(select(ConditionAssessmentModel))).scalar_one().issue_description == "Original damage"
        assert not (await db.execute(select(PhotoModel))).scalars().all()


async def test_broker_failure_preserves_committed_batch_and_prior_publications(
    bulk_api, postgres_sessions, monkeypatch
):
    await seed_filter(postgres_sessions)

    def fail_second_publication(name, *, kwargs, queue):
        if bulk_api.publications:
            raise RuntimeError("broker unavailable")
        bulk_api.publish(name, kwargs=kwargs, queue=queue)

    monkeypatch.setattr(bulk_api.router.celery_app, "send_task", fail_second_publication)
    response = await post_batch(bulk_api, [vehicle("FIRST"), vehicle("SECOND")])
    assert response.status_code == 500
    assert response.json() == {"detail": "Bulk error"}
    assert len(bulk_api.publications) == 1
    assert bulk_api.publications[0][1]["vin"] == "FIRST"
    async with postgres_sessions() as db:
        assert set((await db.execute(select(CarModel.vin))).scalars()) == {"FIRST", "SECOND"}


async def test_bulk_auth_and_validation_contracts(bulk_api):
    response = await bulk_api.client.post("/api/v1/vehicles/bulk", json={"ivent": "updated", "vehicles": []})
    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is missing"}
    response = await bulk_api.client.post(
        "/api/v1/vehicles/bulk", json={"ivent": "updated", "vehicles": []}, headers={"X-Auth-Token": "invalid"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}
    response = await bulk_api.client.post(
        "/api/v1/vehicles/bulk", json={"vehicles": []}, headers={"X-Auth-Token": "test-parsers-token"}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "ivent"]
    assert bulk_api.publications == []
