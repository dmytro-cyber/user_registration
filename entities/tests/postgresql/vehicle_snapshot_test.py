from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select

from crud.vehicle_snapshot import persist_vehicle_snapshot
from models.vehicle import CarModel, ConditionAssessmentModel, PhotoModel, RecommendationStatus, RelevanceStatus
from schemas.vehicle import CarCreateSchema
from services.vehicle_snapshot import save_vehicle_snapshot

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def snapshot(**changes):
    return CarCreateSchema.model_validate(
        {
            "vin": "SNAPSHOT-TRANSACTION",
            "vehicle": "Updated",
            "year": 2018,
            "mileage": 50000,
            "make": "Toyota",
            "model": "Camry",
            "fuel_type": "Gasoline",
            "transmision": "Automatic",
            "current_bid": 2000,
            "photos": [{"url": "https://photos.test/new.jpg"}],
            "condition_assessments": [{"type_of_damage": "Primary", "issue_description": "Mechanical"}],
            **changes,
        }
    )


async def seed_existing(sessions, *, manual=False):
    async with sessions() as db:
        car = CarModel(
            vin="SNAPSHOT-TRANSACTION",
            vehicle="Original",
            current_bid=1000,
            actual_bid=1500,
            suggested_bid=5000,
            recommendation_status=RecommendationStatus.RECOMMENDED,
            recommendation_manually_set=True,
            recommendation_status_reasons="Manual review;",
            is_manually_upserted=manual,
            relevance=RelevanceStatus.ACTIVE,
            is_checked=True,
        )
        db.add(car)
        await db.flush()
        db.add(PhotoModel(car_id=car.id, url="https://photos.test/original.jpg", is_hd=False))
        db.add(ConditionAssessmentModel(car_id=car.id, type_of_damage="Primary", issue_description="Minor Dents"))
        await db.commit()


async def assert_original_state(sessions, *, exists):
    async with sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one_or_none()
        photos = (await db.execute(select(PhotoModel.url))).scalars().all()
        conditions = (await db.execute(select(ConditionAssessmentModel.issue_description))).scalars().all()
        if not exists:
            assert car is None
            assert photos == conditions == []
            return
        assert car.vehicle == "Original"
        assert car.current_bid == 1000
        assert car.recommendation_status == RecommendationStatus.RECOMMENDED
        assert car.recommendation_manually_set
        assert car.recommendation_status_reasons == "Manual review;"
        assert photos == ["https://photos.test/original.jpg"]
        assert conditions == ["Minor Dents"]


async def test_repository_keeps_all_writes_uncommitted_until_caller_decides(postgres_sessions):
    async with postgres_sessions() as db:
        needs_enrichment = await persist_vehicle_snapshot(snapshot(), "initial", db)
        assert needs_enrichment is False
        await db.flush()
        assert (await db.execute(select(CarModel.vin))).scalar_one() == "SNAPSHOT-TRANSACTION"
        await assert_original_state(postgres_sessions, exists=False)
        await db.rollback()
    await assert_original_state(postgres_sessions, exists=False)


@pytest.mark.parametrize("existing", [False, True])
async def test_child_insert_failure_rolls_back_vehicle_and_replaced_conditions(postgres_sessions, existing):
    if existing:
        await seed_existing(postgres_sessions)

    def fail_photo_insert(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO photos"):
            raise RuntimeError("photo write failed")

    engine = postgres_sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", fail_photo_insert)
    try:
        async with postgres_sessions() as db:
            result = await save_vehicle_snapshot(snapshot(), "update", db)
            assert result.saved is False
            assert result.needs_enrichment is False
    finally:
        event.remove(engine, "before_cursor_execute", fail_photo_insert)
    await assert_original_state(postgres_sessions, exists=existing)


@pytest.mark.parametrize("mode", ["create", "update", "manual_bid_only"])
async def test_commit_failure_rolls_back_every_save_branch(postgres_sessions, monkeypatch, mode):
    if mode != "create":
        await seed_existing(postgres_sessions, manual=mode == "manual_bid_only")
    async with postgres_sessions() as db:
        monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
        result = await save_vehicle_snapshot(snapshot(), "update", db)
        assert result.saved is False
        assert result.needs_enrichment is False
    await assert_original_state(postgres_sessions, exists=mode != "create")


async def test_successful_manual_bid_only_save_commits_without_enrichment(postgres_sessions):
    await seed_existing(postgres_sessions, manual=True)
    async with postgres_sessions() as db:
        result = await save_vehicle_snapshot(snapshot(), "update", db)
        assert result.saved is True
        assert result.needs_enrichment is False
    async with postgres_sessions() as db:
        car = (await db.execute(select(CarModel))).scalar_one()
        assert car.current_bid == 2000
        assert car.vehicle == "Original"
        assert car.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
        assert not car.recommendation_manually_set
        assert (await db.execute(select(PhotoModel.url))).scalars().all() == ["https://photos.test/original.jpg"]
