import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from crud.bidding_hub import add_hub_removal_history, get_vehicle_for_bid_update, mark_vehicle_removed_from_hub
from exceptions.bidding import BidVehicleNotFoundError
from models.user import UserModel, UserRoleEnum, UserRoleModel
from models.vehicle import CarInventoryModel, CarModel, CarStatus, HistoryModel, RelevanceStatus
from services import bidding_hub

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def seed(sessions, status=CarStatus.BIDDING):
    async with sessions() as db:
        role = UserRoleModel(name=UserRoleEnum.USER)
        db.add(role)
        await db.flush()
        user = UserModel(email="hub-removal@example.com", _hashed_password="unused", role_id=role.id)
        car = CarModel(
            vin="hub-removal",
            vehicle="car",
            car_status=status,
            actual_bid=0,
            relevance=RelevanceStatus.ARCHIVAL,
            attempts=2,
        )
        db.add_all([user, car])
        await db.flush()
        inventory = CarInventoryModel(car_id=car.id, vin=car.vin, vehicle=car.vehicle, stock="test", vehicle_cost=1000)
        db.add(inventory)
        await db.commit()
        return car.id, user.id, inventory.id


@pytest.mark.parametrize("status", [CarStatus.BIDDING, CarStatus.WON])
async def test_removal_commits_status_and_history_without_deleting_car_or_inventory(postgres_sessions, status):
    car_id, user_id, inventory_id = await seed(postgres_sessions, status)
    async with postgres_sessions() as db:
        assert await bidding_hub.remove_vehicle_from_hub(db, car_id=car_id, user_id=user_id) is None
    async with postgres_sessions() as observer:
        car = await observer.get(CarModel, car_id)
        assert car.car_status == CarStatus.DELETED_FROM_BIDDING_HUB
        assert car.relevance == RelevanceStatus.ARCHIVAL and car.attempts == 2 and car.actual_bid == 0
        inventory = await observer.get(CarInventoryModel, inventory_id)
        assert inventory.car_id == car_id and inventory.vehicle_cost == 1000
        history = (await observer.execute(select(HistoryModel))).scalar_one()
        assert (history.car_id, history.user_id, history.action) == (
            car_id,
            user_id,
            "Deleted vehicle from Bidding Hub",
        )
        assert history.car_inventory_id is None and history.comment is None


async def test_repeated_removal_adds_another_history_entry(postgres_sessions):
    car_id, user_id, _ = await seed(postgres_sessions, CarStatus.DELETED_FROM_BIDDING_HUB)
    for _ in range(2):
        async with postgres_sessions() as db:
            await bidding_hub.remove_vehicle_from_hub(db, car_id=car_id, user_id=user_id)
    async with postgres_sessions() as db:
        assert len((await db.execute(select(HistoryModel))).scalars().all()) == 2


async def test_missing_vehicle_is_rejected_without_commit(postgres_sessions, monkeypatch):
    async with postgres_sessions() as db:
        commit = AsyncMock()
        monkeypatch.setattr(db, "commit", commit)
        with pytest.raises(BidVehicleNotFoundError):
            await bidding_hub.remove_vehicle_from_hub(db, car_id=-1, user_id=-1)
        commit.assert_not_awaited()


@pytest.mark.parametrize("stage", ["status_flush", "history", "commit"])
async def test_failure_rolls_back_status_and_history(postgres_sessions, monkeypatch, stage):
    car_id, user_id, _ = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        if stage == "status_flush":
            monkeypatch.setattr(db, "flush", AsyncMock(side_effect=RuntimeError("failed")))
        elif stage == "history":
            add = bidding_hub.add_hub_removal_history

            def fail_history(*args):
                add(*args)
                raise RuntimeError("failed")

            monkeypatch.setattr(bidding_hub, "add_hub_removal_history", fail_history)
        else:

            async def fail_commit():
                await db.flush()
                raise RuntimeError("failed")

            monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="failed"):
            await bidding_hub.remove_vehicle_from_hub(db, car_id=car_id, user_id=user_id)
    async with postgres_sessions() as db:
        assert (await db.get(CarModel, car_id)).car_status == CarStatus.BIDDING
        assert list((await db.execute(select(HistoryModel))).scalars()) == []


async def test_invalid_history_user_fk_rolls_back_flushed_status(postgres_sessions):
    car_id, _, _ = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        with pytest.raises(IntegrityError):
            await bidding_hub.remove_vehicle_from_hub(db, car_id=car_id, user_id=-1)
    async with postgres_sessions() as db:
        assert (await db.get(CarModel, car_id)).car_status == CarStatus.BIDDING
        assert list((await db.execute(select(HistoryModel))).scalars()) == []


async def test_concurrent_removals_wait_for_row_lock_and_each_log_history(postgres_sessions, monkeypatch):
    car_id, user_id, _ = await seed(postgres_sessions)
    first_ready, release, second_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    second_pid = []

    async def run(first):
        async with postgres_sessions() as db:
            if first:
                commit = db.commit

                async def hold_commit():
                    first_ready.set()
                    await release.wait()
                    await commit()

                monkeypatch.setattr(db, "commit", hold_commit)
            else:
                second_pid.append(await db.scalar(text("SELECT pg_backend_pid()")))
                second_ready.set()
            await bidding_hub.remove_vehicle_from_hub(db, car_id=car_id, user_id=user_id)

    tasks = [asyncio.create_task(run(True))]
    try:
        await asyncio.wait_for(first_ready.wait(), 5)
        tasks.append(asyncio.create_task(run(False)))
        await asyncio.wait_for(second_ready.wait(), 5)
        async with postgres_sessions() as observer:
            async with asyncio.timeout(5):
                while not await observer.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": second_pid[0]}
                ):
                    await asyncio.sleep(0.01)
        release.set()
        await asyncio.gather(*tasks)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    async with postgres_sessions() as db:
        assert (await db.get(CarModel, car_id)).car_status == CarStatus.DELETED_FROM_BIDDING_HUB
        assert len((await db.execute(select(HistoryModel))).scalars().all()) == 2


async def test_crud_status_and_history_writes_do_not_commit(postgres_sessions):
    car_id, user_id, _ = await seed(postgres_sessions)
    async with postgres_sessions() as writer:
        car = await get_vehicle_for_bid_update(writer, car_id)
        await mark_vehicle_removed_from_hub(writer, car)
        add_hub_removal_history(writer, car_id, user_id)
        await writer.flush()
        async with postgres_sessions() as observer:
            assert (await observer.get(CarModel, car_id)).car_status == CarStatus.BIDDING
            assert list((await observer.execute(select(HistoryModel))).scalars()) == []
        await writer.rollback()
