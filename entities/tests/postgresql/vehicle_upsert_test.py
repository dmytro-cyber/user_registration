from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from exceptions.vehicle_ingestion import VehicleUpsertError
from models.vehicle import CarModel, RecommendationStatus
from schemas.vehicle import CarUpsertSchema
from services.vehicle_upsert import upsert_vehicle

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class Publisher:
    def __init__(self, sessions, fail=False):
        self.sessions = sessions
        self.fail = fail
        self.payloads = []

    def publish(self, payload):
        self.payloads.append(payload)
        if self.fail:
            raise RuntimeError("broker unavailable")
        return "enrichment-task"


async def test_create_normalizes_payload_and_publishes_after_commit(postgres_sessions):
    publisher = Publisher(postgres_sessions)
    async with postgres_sessions() as db:
        original = publisher.publish

        def publish(payload):
            assert not db.in_transaction()
            return original(payload)

        publisher.publish = publish
        task = await upsert_vehicle(
            db,
            CarUpsertSchema(
                vin="UPSERT-NEW",
                vehicle="Toyota Camry",
                make="toyota",
                model="camry",
                auction="Copart",
                transmision="Automatic",
            ),
            publisher,
        )
    assert task == "enrichment-task"
    assert publisher.payloads == [
        {
            "vin": "UPSERT-NEW",
            "car_name": "Toyota Camry",
            "car_engine": None,
            "mileage": None,
            "car_make": "Toyota",
            "car_model": "Camry",
            "car_year": None,
            "car_transmison": "automatic",
        }
    ]
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert car.auction == "copart"
        assert car.is_manually_upserted and not car.is_checked and car.attempts == 0


@pytest.mark.parametrize("changed", [False, True])
async def test_checked_vehicle_preserves_manual_decision_and_only_reparses_changed_fields(postgres_sessions, changed):
    async with postgres_sessions() as db:
        db.add(
            CarModel(
                vin="UPSERT-EXISTING",
                vehicle="Original",
                is_checked=True,
                attempts=3,
                recommendation_manually_set=True,
                recommendation_status=RecommendationStatus.RECOMMENDED,
                recommendation_status_reasons="Manual decision",
            )
        )
        await db.commit()
    publisher = Publisher(postgres_sessions)
    async with postgres_sessions() as db:
        await upsert_vehicle(
            db, CarUpsertSchema(vin="UPSERT-EXISTING", vehicle="Changed" if changed else "Original"), publisher
        )
    assert bool(publisher.payloads) == changed
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert car.is_checked == (not changed)
        assert car.attempts == (0 if changed else 3)
        assert car.recommendation_manually_set
        assert car.recommendation_status_reasons == "Manual decision"


async def test_broker_failure_keeps_committed_vehicle(postgres_sessions):
    async with postgres_sessions() as db:
        assert (
            await upsert_vehicle(
                db, CarUpsertSchema(vin="UPSERT-BROKER", vehicle="Test"), Publisher(postgres_sessions, fail=True)
            )
            is None
        )
    async with postgres_sessions() as db:
        assert (await db.execute(select(CarModel.vin))).scalar_one() == "UPSERT-BROKER"


@pytest.mark.parametrize(
    "changes",
    [
        {"fuel_type": "Diesel"},
        {"transmision": "Manual"},
        {"condition_assessments": [{"type_of_damage": "Primary", "issue_description": "Water/Flood"}]},
    ],
)
async def test_new_disqualifying_information_overrides_manual_recommendation(postgres_sessions, changes):
    async with postgres_sessions() as db:
        db.add(
            CarModel(
                vin="UPSERT-OVERRIDE",
                vehicle="Original",
                is_checked=True,
                recommendation_manually_set=True,
                recommendation_status=RecommendationStatus.RECOMMENDED,
                fuel_type="gasoline",
                transmision="automatic",
            )
        )
        await db.commit()
    async with postgres_sessions() as db:
        await upsert_vehicle(db, CarUpsertSchema(vin="UPSERT-OVERRIDE", **changes), Publisher(postgres_sessions))
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert not car.recommendation_manually_set
        assert car.recommendation_status == RecommendationStatus.NOT_RECOMMENDED


async def test_commit_failure_rolls_back_and_never_publishes(postgres_sessions, monkeypatch):
    publisher = Publisher(postgres_sessions)
    async with postgres_sessions() as db:
        monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
        with pytest.raises(VehicleUpsertError, match="commit failed"):
            await upsert_vehicle(db, CarUpsertSchema(vin="UPSERT-ROLLBACK", vehicle="Test"), publisher)
    assert publisher.payloads == []
    async with postgres_sessions() as db:
        assert (await db.execute(select(CarModel))).scalars().all() == []
