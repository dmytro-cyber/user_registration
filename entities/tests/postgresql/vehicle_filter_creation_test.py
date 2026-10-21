import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text

from crud.vehicle_filters import activate_vehicles_for_created_filter, insert_vehicle_filter
from models.admin import FilterModel
from models.filter_kickoff_queue import (
    FILTER_KICKOFF_INTERVAL,
    FILTER_QUEUE_ENQUEUE_LOCK_KEY,
    FilterKickoffQueueModel,
    FilterKickoffQueueStatus,
)
from models.vehicle import CarModel, CarStatus, RelevanceStatus
from schemas.admin import FilterCreate
from services import vehicle_filters

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def payload(model=None):
    return FilterCreate(make="Toyota", model=model, year_from=2010, year_to=2020, odometer_min=10, odometer_max=100)


async def seed(sessions):
    async with sessions() as db:
        for vin, changes in [
            ("lower", {"year": 2010, "mileage": 10}),
            (
                "won-upper",
                {"year": 2020, "mileage": 100, "car_status": CarStatus.WON, "relevance": RelevanceStatus.ARCHIVAL},
            ),
            ("other-model", {"model": "Other"}),
            ("other-make", {"make": "Honda"}),
            ("too-old", {"year": 2009}),
            ("too-new", {"year": 2021}),
            ("low-mileage", {"mileage": 9}),
            ("high-mileage", {"mileage": 101}),
            ("no-year", {"year": None}),
            ("no-mileage", {"mileage": None}),
        ]:
            values = dict(
                vin=vin,
                vehicle="car",
                make="Toyota",
                model="Camry",
                year=2015,
                mileage=50,
                relevance=RelevanceStatus.IRRELEVANT,
                attempts=2,
                is_checked=True,
            )
            values.update(changes)
            db.add(CarModel(**values))
        await db.commit()


@pytest.mark.parametrize("model", [None, "Camry"])
async def test_filter_vehicle_updates_and_queue_commit_together(postgres_sessions, model):
    await seed(postgres_sessions)
    before = datetime.now()
    async with postgres_sessions() as db:
        saved = await vehicle_filters.create_filter_and_schedule(db, payload(model))
        filter_id = saved.id
        assert saved.updated_at.tzinfo is None and before <= saved.updated_at <= datetime.now()
    async with postgres_sessions() as observer:
        cars = {car.vin: car for car in (await observer.execute(select(CarModel))).scalars()}
        active = {vin for vin, car in cars.items() if car.relevance == RelevanceStatus.ACTIVE}
        assert active == ({"lower", "won-upper", "other-model"} if model is None else {"lower", "won-upper"})
        assert all(car.attempts == 2 and car.is_checked for car in cars.values())
        assert cars["won-upper"].car_status == CarStatus.WON
        job = (await observer.execute(select(FilterKickoffQueueModel))).scalar_one()
        assert job.filter_id == filter_id and job.status == FilterKickoffQueueStatus.SCHEDULED
        assert job.attempts == 0 and job.scheduled_at.tzinfo is not None


async def test_falsy_upper_bounds_keep_legacy_defaults_and_null_make_matches_null_make(postgres_sessions):
    async with postgres_sessions() as db:
        for vin, changes in [
            ("zero", {"year": 0, "mileage": 0}),
            ("max", {"year": 3000, "mileage": 10000000}),
            ("year-out", {"year": 3001}),
            ("mileage-out", {"mileage": 10000001}),
            ("make-out", {"make": "Toyota"}),
        ]:
            values = dict(
                vin=vin, vehicle="car", make=None, year=2015, mileage=100, relevance=RelevanceStatus.IRRELEVANT
            )
            values.update(changes)
            db.add(CarModel(**values))
        await db.commit()
    async with postgres_sessions() as db:
        await vehicle_filters.create_filter_and_schedule(db, FilterCreate(year_to=0, odometer_max=0))
    async with postgres_sessions() as db:
        assert set(
            (await db.execute(select(CarModel.vin).where(CarModel.relevance == RelevanceStatus.ACTIVE))).scalars()
        ) == {"zero", "max"}


@pytest.mark.parametrize("stage", ["activation", "queue", "commit"])
async def test_failure_rolls_back_filter_relevance_and_queue_and_releases_lock(postgres_sessions, monkeypatch, stage):
    await seed(postgres_sessions)
    async with postgres_sessions() as db:
        if stage == "commit":
            monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("failed")))
        else:
            name = "activate_vehicles_for_created_filter" if stage == "activation" else "enqueue_filter_kickoff"
            original = getattr(vehicle_filters, name)

            async def fail_after_write(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("failed")

            monkeypatch.setattr(vehicle_filters, name, fail_after_write)
        with pytest.raises(RuntimeError, match="failed"):
            await vehicle_filters.create_filter_and_schedule(db, payload())
    async with postgres_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(FilterModel)) == 0
        assert await db.scalar(select(func.count()).select_from(FilterKickoffQueueModel)) == 0
        assert (
            set((await db.execute(select(CarModel.vin).where(CarModel.relevance == RelevanceStatus.ACTIVE))).scalars())
            == set()
        )
        assert await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": FILTER_QUEUE_ENQUEUE_LOCK_KEY})


async def test_refresh_failure_after_commit_retains_committed_filter_vehicles_and_queue(
    postgres_sessions, monkeypatch
):
    await seed(postgres_sessions)
    async with postgres_sessions() as db:
        monkeypatch.setattr(db, "refresh", AsyncMock(side_effect=RuntimeError("refresh failed")))
        with pytest.raises(RuntimeError, match="refresh failed"):
            await vehicle_filters.create_filter_and_schedule(db, payload())
    async with postgres_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(FilterModel)) == 1
        assert await db.scalar(select(func.count()).select_from(FilterKickoffQueueModel)) == 1
        assert (
            await db.scalar(
                select(func.count()).select_from(CarModel).where(CarModel.relevance == RelevanceStatus.ACTIVE)
            )
            == 3
        )


async def test_crud_does_not_commit_filter_or_relevance(postgres_sessions):
    await seed(postgres_sessions)
    async with postgres_sessions() as writer, postgres_sessions() as observer:
        db_filter = await insert_vehicle_filter(writer, payload().model_dump(exclude_unset=True), datetime.now())
        await activate_vehicles_for_created_filter(writer, db_filter)
        assert await observer.scalar(select(func.count()).select_from(FilterModel)) == 0
        assert (
            await observer.scalar(
                select(func.count()).select_from(CarModel).where(CarModel.relevance == RelevanceStatus.ACTIVE)
            )
            == 0
        )
        await writer.rollback()
    async with postgres_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(FilterModel)) == 0


async def test_concurrent_creation_preserves_queue_interval(postgres_sessions):
    async def create(make):
        async with postgres_sessions() as db:
            return (await vehicle_filters.create_filter_and_schedule(db, FilterCreate(make=make))).id

    ids = await asyncio.gather(create("Toyota"), create("Honda"))
    async with postgres_sessions() as db:
        jobs = (
            (await db.execute(select(FilterKickoffQueueModel).order_by(FilterKickoffQueueModel.scheduled_at)))
            .scalars()
            .all()
        )
        assert {job.filter_id for job in jobs} == set(ids)
        assert jobs[1].scheduled_at - jobs[0].scheduled_at == FILTER_KICKOFF_INTERVAL
