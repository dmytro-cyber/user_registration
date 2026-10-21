from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from crud.vehicle_filters import adjust_updated_filter_relevance
from db.config import DatabaseConfig
from db.factories import create_sync_database
from exceptions.vehicle_filters import VehicleFilterNotFound
from models.admin import FilterModel
from models.filter_kickoff_queue import FilterKickoffQueueModel, FilterKickoffQueueStatus
from models.vehicle import AutoCheckModel, CarModel, CarStatus, RelevanceStatus
from schemas.admin import FilterUpdate
from services import vehicle_filters

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
STAMP = datetime(2026, 1, 1)


async def seed(sessions):
    async with sessions() as db:
        filt = FilterModel(
            make="Toyota",
            model="Camry",
            year_from=2010,
            year_to=2020,
            odometer_min=10,
            odometer_max=100,
            updated_at=STAMP,
        )
        db.add(filt)
        await db.flush()
        db.add(
            FilterKickoffQueueModel(
                filter_id=filt.id,
                status=FilterKickoffQueueStatus.SCHEDULED,
                scheduled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        for vin, model, relevance, changes in [
            ("old-active", "Camry", RelevanceStatus.ACTIVE, {}),
            ("old-archival", "Camry", RelevanceStatus.ARCHIVAL, {}),
            ("old-won-active", "Camry", RelevanceStatus.ACTIVE, {"car_status": CarStatus.WON}),
            ("old-won-archival", "Camry", RelevanceStatus.ARCHIVAL, {"car_status": CarStatus.WON}),
            ("old-irrelevant", "Camry", RelevanceStatus.IRRELEVANT, {}),
            ("new-irrelevant", "Corolla", RelevanceStatus.IRRELEVANT, {}),
            ("new-won", "Corolla", RelevanceStatus.IRRELEVANT, {"car_status": CarStatus.WON}),
            ("new-archival", "Corolla", RelevanceStatus.ARCHIVAL, {}),
            ("new-null", "Corolla", None, {}),
            (
                "outside-null-fields",
                "Other",
                RelevanceStatus.IRRELEVANT,
                {"make": "Honda", "year": None, "mileage": None},
            ),
        ]:
            values = dict(
                vin=vin, vehicle="car", make="Toyota", model=model, year=2015, mileage=50, relevance=relevance
            )
            values.update(changes)
            db.add(CarModel(**values))
        await db.commit()
        return filt.id


async def states(sessions):
    async with sessions() as db:
        return {car.vin: car.relevance for car in (await db.execute(select(CarModel))).scalars()}


async def test_update_preserves_delta_rules_and_publishes_after_both_commits(postgres_sessions):
    filter_id = await seed(postgres_sessions)
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    observed = []

    def publish(published_id):
        with database.sessions() as observer:
            observed.append(
                (
                    published_id,
                    observer.get(FilterModel, filter_id).model,
                    observer.scalar(select(CarModel.relevance).where(CarModel.vin == "new-irrelevant")),
                    observer.scalar(select(CarModel.id).where(CarModel.vin == "old-archival")),
                )
            )

    try:
        async with postgres_sessions() as db:
            assert await vehicle_filters.update_filter_and_schedule(
                db, filter_id, FilterUpdate(model="Corolla"), Mock(publish=publish)
            ) == {
                "detail": "Filter updated, relevance adjusted, kickoff scheduled",
            }
    finally:
        database.engine.dispose()
    assert observed == [(filter_id, "Corolla", RelevanceStatus.ACTIVE, None)]
    current = await states(postgres_sessions)
    assert current["old-active"] == RelevanceStatus.IRRELEVANT and "old-archival" not in current
    assert (
        current["old-won-active"] == RelevanceStatus.ACTIVE and current["old-won-archival"] == RelevanceStatus.ARCHIVAL
    )
    assert current["new-irrelevant"] == current["new-won"] == RelevanceStatus.ACTIVE
    assert current["new-archival"] == RelevanceStatus.ARCHIVAL and current["new-null"] is None
    async with postgres_sessions() as db:
        assert (await db.get(FilterModel, filter_id)).updated_at == STAMP
        jobs = (await db.execute(select(FilterKickoffQueueModel))).scalars().all()
        assert len(jobs) == 1 and jobs[0].status == FilterKickoffQueueStatus.SCHEDULED


async def test_explicit_none_empty_and_zero_remove_update_query_constraints(postgres_sessions):
    filter_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        await vehicle_filters.update_filter_and_schedule(
            db,
            filter_id,
            FilterUpdate(make=None, model="", year_from=0, year_to=0, odometer_min=0, odometer_max=0),
            Mock(),
        )
    current = await states(postgres_sessions)
    assert current["outside-null-fields"] == RelevanceStatus.ACTIVE
    assert current["old-irrelevant"] == RelevanceStatus.IRRELEVANT  # Existing matches are not reactivated.
    async with postgres_sessions() as db:
        saved = await db.get(FilterModel, filter_id)
        assert saved.make is None and saved.model == "" and saved.year_to == 0


async def test_empty_patch_still_commits_twice_and_publishes(postgres_sessions, monkeypatch):
    filter_id = await seed(postgres_sessions)
    before = await states(postgres_sessions)
    publisher = Mock()
    async with postgres_sessions() as db:
        commit = AsyncMock(wraps=db.commit)
        monkeypatch.setattr(db, "commit", commit)
        await vehicle_filters.update_filter_and_schedule(db, filter_id, FilterUpdate(), publisher)
        assert commit.await_count == 2
    publisher.publish.assert_called_once_with(filter_id)
    assert await states(postgres_sessions) == before


async def test_missing_filter_raises_application_error_without_publication(postgres_sessions):
    publisher = Mock()
    async with postgres_sessions() as db:
        with pytest.raises(VehicleFilterNotFound):
            await vehicle_filters.update_filter_and_schedule(db, -1, FilterUpdate(), publisher)
    publisher.publish.assert_not_called()


@pytest.mark.parametrize("stage", ["first_commit", "refresh", "relevance", "second_commit"])
async def test_failure_preserves_first_commit_boundary(postgres_sessions, monkeypatch, stage):
    filter_id = await seed(postgres_sessions)
    before = await states(postgres_sessions)
    publisher = Mock()
    async with postgres_sessions() as db:
        if stage in {"first_commit", "second_commit"}:
            commit = db.commit
            calls = 0

            async def fail_commit():
                nonlocal calls
                calls += 1
                if calls == (1 if stage == "first_commit" else 2):
                    raise RuntimeError("failed")
                await commit()

            monkeypatch.setattr(db, "commit", fail_commit)
        elif stage == "refresh":
            monkeypatch.setattr(db, "refresh", AsyncMock(side_effect=RuntimeError("failed")))
        else:
            adjust = vehicle_filters.adjust_updated_filter_relevance

            async def fail_adjust(*args):
                await adjust(*args)
                raise RuntimeError("failed")

            monkeypatch.setattr(vehicle_filters, "adjust_updated_filter_relevance", fail_adjust)
        with pytest.raises(RuntimeError, match="failed"):
            await vehicle_filters.update_filter_and_schedule(db, filter_id, FilterUpdate(model="Corolla"), publisher)
    publisher.publish.assert_not_called()
    assert await states(postgres_sessions) == before
    async with postgres_sessions() as db:
        assert (await db.get(FilterModel, filter_id)).model == ("Camry" if stage == "first_commit" else "Corolla")


async def test_broker_failure_keeps_both_committed_phases(postgres_sessions):
    filter_id = await seed(postgres_sessions)
    publisher = Mock()
    publisher.publish.side_effect = RuntimeError("broker failed")
    async with postgres_sessions() as db:
        with pytest.raises(RuntimeError, match="broker failed"):
            await vehicle_filters.update_filter_and_schedule(db, filter_id, FilterUpdate(model="Corolla"), publisher)
    current = await states(postgres_sessions)
    assert current["new-irrelevant"] == RelevanceStatus.ACTIVE and "old-archival" not in current
    async with postgres_sessions() as db:
        assert (await db.get(FilterModel, filter_id)).model == "Corolla"


async def test_foreign_key_delete_failure_rolls_back_relevance_but_not_filter(postgres_sessions):
    filter_id = await seed(postgres_sessions)
    before = await states(postgres_sessions)
    async with postgres_sessions() as db:
        archived_id = await db.scalar(select(CarModel.id).where(CarModel.vin == "old-archival"))
        db.add(AutoCheckModel(car_id=archived_id))
        await db.commit()
    publisher = Mock()
    async with postgres_sessions() as db:
        with pytest.raises(IntegrityError):
            await vehicle_filters.update_filter_and_schedule(db, filter_id, FilterUpdate(model="Corolla"), publisher)
    assert await states(postgres_sessions) == before
    publisher.publish.assert_not_called()
    async with postgres_sessions() as db:
        assert (await db.get(FilterModel, filter_id)).model == "Corolla"


async def test_relevance_crud_does_not_commit(postgres_sessions):
    await seed(postgres_sessions)
    before = await states(postgres_sessions)
    async with postgres_sessions() as db:
        activate_id = await db.scalar(select(CarModel.id).where(CarModel.vin == "new-irrelevant"))
        remove_id = await db.scalar(select(CarModel.id).where(CarModel.vin == "old-active"))
        await adjust_updated_filter_relevance(db, {activate_id}, {remove_id})
        await db.rollback()
    assert await states(postgres_sessions) == before
