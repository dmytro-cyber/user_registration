from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from crud.bidding_hub import get_bidding_hub_vehicles
from exceptions.bidding import EmptyBiddingHubError, InvalidBiddingHubSortError
from models.user import UserModel, UserRoleEnum, UserRoleModel
from models.vehicle import CarModel, CarStatus, HistoryModel
from services.bidding_hub import list_bidding_hub

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def seed(sessions):
    async with sessions() as db:
        role = UserRoleModel(name=UserRoleEnum.USER)
        db.add(role)
        await db.flush()
        users = [UserModel(email=email, _hashed_password="unused", role_id=role.id,
                           first_name=name, last_name="User")
                 for email, name in [("a@example.com", "Alpha"), ("z@example.com", "Zulu")]]
        cars = [CarModel(vin=f"list-{i}", vehicle=f"car-{i}", car_status=status,
                         current_bid=100 + i, location="Place", seller="Seller")
                for i, status in enumerate(CarStatus)]
        db.add_all(users + cars)
        await db.flush()
        db.add_all([
            HistoryModel(car_id=cars[1].id, user_id=users[1].id, action="old", created_at=datetime(2020, 1, 1)),
            HistoryModel(car_id=cars[1].id, user_id=users[0].id, action="new", created_at=datetime(2022, 1, 1)),
            HistoryModel(car_id=cars[2].id, user_id=users[1].id, action="only", created_at=datetime(2021, 1, 1)),
        ])
        await db.commit()
        return [car.id for car in cars]


async def test_list_membership_pagination_mapping_and_session_ownership(postgres_sessions):
    ids = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        db.close = AsyncMock(side_effect=AssertionError("CRUD closed caller session"))
        db.commit = AsyncMock(side_effect=AssertionError("read committed"))
        db.rollback = AsyncMock(side_effect=AssertionError("read rolled back"))
        response = await list_bidding_hub(db, page=2, page_size=2, sort_by="vehicle", sort_order="asc")
        assert [car.id for car in response.vehicles] == ids[3:5]
        assert response.total_count == 4 and response.total_pages == 2
        assert response.vehicles[0].auction_name == "Place / Seller"
        assert response.vehicles[0].last_user is None
        with pytest.raises(EmptyBiddingHubError):
            await list_bidding_hub(db, page=3, page_size=2, sort_by="vehicle", sort_order="asc")
        db.close = AsyncMock(wraps=db.__class__.close.__get__(db))


@pytest.mark.parametrize("direction,expected", [("asc", [1, 2]), ("desc", [2, 1])])
async def test_user_sort_uses_latest_author_and_excludes_authorless_cars(postgres_sessions, direction, expected):
    ids = await seed(postgres_sessions)
    async with postgres_sessions() as db:
        rows, count, pages = await get_bidding_hub_vehicles(db, 1, 10, "user", direction)
    assert [car.id for car in rows] == [ids[i] for i in expected]
    assert count == 2 and pages == 1
    assert rows[0].bidding_hub_history[0].user.first_name is not None


@pytest.mark.parametrize("field", ["vehicle", "auction", "location", "date", "lot", "avg_market_price",
    "predicted_total_investments", "predicted_profit_margin", "predicted_roi", "actual_bid", "status",
    "current_bid", "suggested_bid"])
async def test_all_existing_sort_fields_remain_supported(postgres_sessions, field):
    await seed(postgres_sessions)
    async with postgres_sessions() as db:
        rows, count, pages = await get_bidding_hub_vehicles(db, 1, 10, field, "desc")
    assert len(rows) == count == 4 and pages == 1


async def test_invalid_sort_remains_rejected_even_on_empty_database(postgres_sessions):
    async with postgres_sessions() as db:
        with pytest.raises(InvalidBiddingHubSortError):
            await list_bidding_hub(db, page=1, page_size=10, sort_by="currnent_bid", sort_order="desc")
