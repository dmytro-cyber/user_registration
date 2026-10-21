from unittest.mock import AsyncMock

import pytest

from crud.vehicle_costs import apply_vehicle_costs, get_vehicle_for_cost_update
from exceptions.vehicle_costs import CostVehicleNotFoundError, EmptyVehicleCostsError, VehicleCostsPersistenceError
from models.vehicle import CarModel
from services.vehicle_costs import update_vehicle_costs

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def seed(sessions, predicted=1000):
    async with sessions() as db:
        car = CarModel(vin="cost-test", vehicle="car", maintenance=100, transportation=50, labor=25,
                       parts_cost=10, auction_fee=15, predicted_total_investments=predicted, suggested_bid=800)
        db.add(car)
        await db.commit()
        return car.id


@pytest.mark.parametrize("fields,predicted,expected", [
    ({"maintenance": None}, 1000, 900),
    ({"labor": 0, "transportation": -20}, 1000, 895),
    ({"maintenance": 12.345}, None, 800),
])
async def test_cost_values_and_suggested_bid_policy(postgres_sessions, fields, predicted, expected):
    car_id = await seed(postgres_sessions, predicted)
    async with postgres_sessions() as db:
        result = await update_vehicle_costs(db, car_id=car_id, fields=fields)
        assert set(result) == {"id", "maintenance", "transportation", "labor", "suggested_bid"}
        assert result["suggested_bid"] == expected
    async with postgres_sessions() as db:
        car = await db.get(CarModel, car_id)
        assert car.suggested_bid == expected
        assert car.parts_cost == 10 and car.auction_fee == 15
        for key, value in fields.items():
            assert getattr(car, key) == value


async def test_cost_missing_and_empty_precedence_without_commit(postgres_sessions):
    car_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        db.commit = AsyncMock(side_effect=AssertionError("unexpected commit"))
        with pytest.raises(CostVehicleNotFoundError):
            await update_vehicle_costs(db, car_id=999999, fields={})
        with pytest.raises(EmptyVehicleCostsError):
            await update_vehicle_costs(db, car_id=car_id, fields={})


async def test_cost_commit_failure_rolls_back_flushed_values(postgres_sessions):
    car_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        async def fail():
            await db.flush()
            raise RuntimeError("failed")
        db.commit = AsyncMock(side_effect=fail)
        with pytest.raises(VehicleCostsPersistenceError):
            await update_vehicle_costs(db, car_id=car_id, fields={"maintenance": 999})
        assert not db.in_transaction()
    async with postgres_sessions() as db:
        car = await db.get(CarModel, car_id)
        assert car.maintenance == 100 and car.suggested_bid == 800


async def test_cost_refresh_failure_retains_committed_update(postgres_sessions):
    car_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        db.refresh = AsyncMock(side_effect=RuntimeError("refresh failed"))
        with pytest.raises(VehicleCostsPersistenceError):
            await update_vehicle_costs(db, car_id=car_id, fields={"maintenance": 200})
    async with postgres_sessions() as db:
        car = await db.get(CarModel, car_id)
        assert car.maintenance == 200 and car.suggested_bid == 700


async def test_cost_crud_does_not_commit(postgres_sessions):
    car_id = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        car = await get_vehicle_for_cost_update(db, car_id)
        apply_vehicle_costs(car, {"maintenance": 200})
        await db.flush()
        async with postgres_sessions() as observer:
            assert (await observer.get(CarModel, car_id)).maintenance == 100
        await db.rollback()
