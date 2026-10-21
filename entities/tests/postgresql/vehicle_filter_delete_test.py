from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from crud.vehicle_filters import adjust_vehicles_for_deleted_filter, remove_vehicle_filter
from domain.vehicle_filters import FilterDeletionResult
from exceptions.vehicle_filters import VehicleFilterNotFound
from models.admin import FilterModel
from models.filter_kickoff_queue import FilterKickoffQueueModel, FilterKickoffQueueStatus
from models.vehicle import AutoCheckModel, CarModel, CarStatus, PhotoModel, RelevanceStatus
from services import vehicle_filters

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def seed(sessions, model="Camry"):
    async with sessions() as db:
        filt = FilterModel(make="Toyota", model=model, year_from=2010, year_to=2020, odometer_min=10, odometer_max=100)
        db.add(filt)
        await db.flush()
        db.add(
            FilterKickoffQueueModel(
                filter_id=filt.id,
                status=FilterKickoffQueueStatus.DISPATCHED,
                scheduled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        for vin, relevance, changes in [
            ("active", RelevanceStatus.ACTIVE, {"year": 2010, "mileage": 10}),
            ("archival", RelevanceStatus.ARCHIVAL, {"year": 2020, "mileage": 100}),
            ("won-active", RelevanceStatus.ACTIVE, {"car_status": CarStatus.WON}),
            ("won-archival", RelevanceStatus.ARCHIVAL, {"car_status": CarStatus.WON}),
            ("irrelevant", RelevanceStatus.IRRELEVANT, {}),
            ("null-relevance", None, {}),
            ("other-model", RelevanceStatus.ACTIVE, {"model": "Other"}),
            ("other-make", RelevanceStatus.ACTIVE, {"make": "Honda"}),
            ("old", RelevanceStatus.ACTIVE, {"year": 2009}),
            ("new", RelevanceStatus.ACTIVE, {"year": 2021}),
            ("low-mileage", RelevanceStatus.ACTIVE, {"mileage": 9}),
            ("high-mileage", RelevanceStatus.ACTIVE, {"mileage": 101}),
            ("no-year", RelevanceStatus.ACTIVE, {"year": None}),
            ("no-mileage", RelevanceStatus.ACTIVE, {"mileage": None}),
        ]:
            values = dict(
                vin=vin, vehicle="car", make="Toyota", model=model, year=2015, mileage=50, relevance=relevance
            )
            values.update(changes)
            db.add(CarModel(**values))
        await db.flush()
        archived_id = await db.scalar(select(CarModel.id).where(CarModel.vin == "archival"))
        db.add(PhotoModel(car_id=archived_id, url="http://local/photo"))
        await db.commit()
        return filt.id


async def state(sessions):
    async with sessions() as db:
        cars = {car.vin: car.relevance for car in (await db.execute(select(CarModel))).scalars()}
        filters = list((await db.execute(select(FilterModel.id))).scalars())
        jobs = list((await db.execute(select(FilterKickoffQueueModel.id))).scalars())
        photos = list((await db.execute(select(PhotoModel.id))).scalars())
        return cars, filters, jobs, photos


@pytest.mark.parametrize("model", ["Camry", None])
async def test_delete_exact_selection_won_protection_and_existing_cascades(postgres_sessions, model):
    filter_id = await seed(postgres_sessions, model)
    before = await state(postgres_sessions)
    async with postgres_sessions() as db:
        assert await vehicle_filters.delete_filter_and_adjust(db, filter_id) == FilterDeletionResult(1, 1)
    cars, filters, jobs, photos = await state(postgres_sessions)
    expected = {**before[0], "active": RelevanceStatus.IRRELEVANT}
    del expected["archival"]
    assert cars == expected
    assert filters == jobs == photos == []


async def test_null_make_model_and_falsy_bounds_keep_delete_defaults(postgres_sessions):
    async with postgres_sessions() as db:
        filt = FilterModel(make=None, model=None, year_to=0, odometer_max=0)
        db.add(filt)
        for vin, changes in [
            ("zero", {"year": 0, "mileage": 0}),
            ("max", {"year": 3000, "mileage": 10000000}),
            ("year-out", {"year": 3001}),
            ("mileage-out", {"mileage": 10000001}),
            ("make-out", {"make": "Toyota"}),
            ("model-out", {"model": "Camry"}),
        ]:
            values = dict(
                vin=vin, vehicle="car", make=None, model=None, year=2015, mileage=100, relevance=RelevanceStatus.ACTIVE
            )
            values.update(changes)
            db.add(CarModel(**values))
        await db.commit()
        filter_id = filt.id
    async with postgres_sessions() as db:
        assert await vehicle_filters.delete_filter_and_adjust(db, filter_id) == FilterDeletionResult(0, 2)
    cars, *_ = await state(postgres_sessions)
    assert {vin for vin, relevance in cars.items() if relevance == RelevanceStatus.IRRELEVANT} == {"zero", "max"}


@pytest.mark.parametrize("stage", ["adjust", "filter_delete", "commit"])
async def test_failure_rolls_back_car_filter_queue_and_cascade_changes(postgres_sessions, monkeypatch, stage):
    filter_id = await seed(postgres_sessions)
    before = await state(postgres_sessions)
    async with postgres_sessions() as db:
        if stage == "commit":

            async def fail_commit():
                await db.flush()
                raise RuntimeError("failed")

            monkeypatch.setattr(db, "commit", fail_commit)
        else:
            name = "adjust_vehicles_for_deleted_filter" if stage == "adjust" else "remove_vehicle_filter"
            original = getattr(vehicle_filters, name)

            async def fail_after_write(*args):
                await original(*args)
                await db.flush()
                raise RuntimeError("failed")

            monkeypatch.setattr(vehicle_filters, name, fail_after_write)
        with pytest.raises(RuntimeError, match="failed"):
            await vehicle_filters.delete_filter_and_adjust(db, filter_id)
    assert await state(postgres_sessions) == before


async def test_archived_auto_check_fk_failure_retains_entire_transaction(postgres_sessions):
    filter_id = await seed(postgres_sessions)
    before = await state(postgres_sessions)
    async with postgres_sessions() as db:
        archived_id = await db.scalar(select(CarModel.id).where(CarModel.vin == "archival"))
        db.add(AutoCheckModel(car_id=archived_id))
        await db.commit()
    async with postgres_sessions() as db:
        with pytest.raises(IntegrityError):
            await vehicle_filters.delete_filter_and_adjust(db, filter_id)
    assert await state(postgres_sessions) == before
    async with postgres_sessions() as db:
        assert (await db.execute(select(AutoCheckModel))).scalar_one().car_id == archived_id


async def test_missing_filter_does_not_commit(postgres_sessions, monkeypatch):
    async with postgres_sessions() as db:
        commit = AsyncMock()
        monkeypatch.setattr(db, "commit", commit)
        with pytest.raises(VehicleFilterNotFound):
            await vehicle_filters.delete_filter_and_adjust(db, -1)
        commit.assert_not_awaited()


async def test_no_matches_still_deletes_filter_and_its_queue(postgres_sessions):
    filter_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        (await db.get(FilterModel, filter_id)).make = "No matches"
        await db.commit()
    before = await state(postgres_sessions)
    async with postgres_sessions() as db:
        assert await vehicle_filters.delete_filter_and_adjust(db, filter_id) == FilterDeletionResult(0, 0)
    cars, filters, jobs, photos = await state(postgres_sessions)
    assert cars == before[0] and photos == before[3] and filters == jobs == []


async def test_crud_does_not_commit_deletions_or_relevance(postgres_sessions):
    filter_id = await seed(postgres_sessions)
    before = await state(postgres_sessions)
    async with postgres_sessions() as writer:
        filt = await writer.get(FilterModel, filter_id)
        assert await adjust_vehicles_for_deleted_filter(writer, filt) == FilterDeletionResult(1, 1)
        await remove_vehicle_filter(writer, filt)
        await writer.flush()
        assert await state(postgres_sessions) == before
        await writer.rollback()
    assert await state(postgres_sessions) == before
