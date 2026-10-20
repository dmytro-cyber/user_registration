import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from models.user import UserModel, UserRoleEnum, UserRoleModel
from models.vehicle import CarModel, FeeModel, HistoryModel
from services.bidding_hub import update_actual_bid

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
async def bid_records(postgres_sessions):
    async with postgres_sessions() as db:
        role = UserRoleModel(name=UserRoleEnum.USER)
        user = UserModel(email="bid@example.com", _hashed_password="unused-test-hash", role=role)
        car = CarModel(
            vin="TESTVIN0000000001", vehicle="Test vehicle", auction="Copart",
            actual_bid=1000, auction_fee=200, transportation=100, avg_market_price=12000,
        )
        db.add_all([
            user, car,
            FeeModel(auction="copart", fee_type="fixed", amount=100, percent=False, price_from=0, price_to=10000),
            FeeModel(auction="COPART", fee_type="percent", amount=10, percent=True, price_from=0, price_to=10000),
        ])
        await db.commit()
        return car.id, user.id


async def test_concurrent_bid_updates_observe_committed_previous_bid(postgres_sessions, bid_records, monkeypatch):
    car_id, user_id = bid_records
    ready = asyncio.Event()
    release = asyncio.Event()

    async with postgres_sessions() as first, postgres_sessions() as second, postgres_sessions() as observer:
        commit = first.commit

        async def pause_first_commit():
            ready.set()
            await release.wait()
            await commit()

        monkeypatch.setattr(first, "commit", pause_first_commit)
        second_pid = (await second.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        updates = []
        try:
            updates.append(asyncio.create_task(update_actual_bid(
                first, car_id=car_id, actual_bid=2000.0, user_id=user_id, comment="first",
            )))
            await asyncio.wait_for(ready.wait(), timeout=5)
            updates.append(asyncio.create_task(update_actual_bid(
                second, car_id=car_id, actual_bid=3000.0, user_id=user_id, comment="second",
            )))
            # Observe the actual database wait, rather than relying on a timed sleep.
            async with asyncio.timeout(5):
                while not (await observer.execute(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": second_pid},
                )).scalar_one():
                    await asyncio.sleep(0.01)
            assert not updates[1].done()
            release.set()
            await asyncio.wait_for(asyncio.gather(*updates), timeout=5)
        finally:
            release.set()
            for task in updates:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*updates, return_exceptions=True)

    async with postgres_sessions() as db:
        car = await db.get(CarModel, car_id)
        assert car.actual_bid == 3000
        assert car.auction_fee == 400
        assert car.profit_margin == 8500
        assert car.roi == pytest.approx(8500 / 3500 * 100)
        history = (await db.execute(
            select(HistoryModel).where(HistoryModel.car_id == car_id).order_by(HistoryModel.id)
        )).scalars().all()
        assert [row.action for row in history] == [
            "Updated actual bid from 1000.0 to 2000.0",
            "Updated actual bid from 2000.0 to 3000.0",
        ]
        assert [row.comment for row in history] == ["first", "second"]


async def test_failed_history_insert_rolls_back_bid_and_releases_lock(postgres_sessions, bid_records):
    car_id, user_id = bid_records
    async with postgres_sessions() as failing:
        with pytest.raises(IntegrityError):
            await update_actual_bid(
                failing, car_id=car_id, actual_bid=5000.0, user_id=999999, comment="invalid user",
            )

        async with postgres_sessions() as db:
            car = await db.get(CarModel, car_id)
            assert car.actual_bid == 1000
            assert car.auction_fee == 200
            assert car.roi is None
            assert car.profit_margin is None
            assert not (await db.execute(select(HistoryModel))).scalars().all()
            # A fresh transaction can acquire the row lock while failing remains open.
            await asyncio.wait_for(update_actual_bid(
                db, car_id=car_id, actual_bid=2000.0, user_id=user_id, comment="recovered",
            ), timeout=5)
