# entities/tests/integration/api/test_inventory.py
"""
Integration tests for inventory history endpoints.

Covered endpoints (note the missing leading slash in the router paths):
- GET /api/v1/inventoryvehicles/history/{car_inventory_id}
- GET /api/v1/inventoryvehicles/history/{part_inventory_id}

Notes:
- We override get_current_user with a lightweight object to avoid auth/token parsing.
- We insert HistoryModel rows directly (with FK checks disabled in SQLite) to avoid
  creating full related entities.
- We verify both 404 (no history) and 200 (history present) behavior.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_current_user
from main import app
from models.vehicle import HistoryModel

pytestmark = pytest.mark.anyio

INVENTORY_PREFIX_NO_SLASH = "/api/v1/inventory"  # router prefix is "/inventory"
# The two history endpoints in the router lack a leading slash in the path,
# so FastAPI registers them as "/api/v1/inventoryvehicles/history/{...}"
CAR_HISTORY_URL = f"{INVENTORY_PREFIX_NO_SLASH}vehicles/history"
PART_HISTORY_URL = f"{INVENTORY_PREFIX_NO_SLASH}parts/history"


@pytest.fixture
def use_test_user(test_user):
    """
    Override get_current_user to return a simple object with the test user's id.
    """
    user_id_captured = int(test_user.id)
    app.dependency_overrides[get_current_user] = (lambda uid=user_id_captured: SimpleNamespace(id=uid))
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def _disable_fk(db_session: AsyncSession):
    await db_session.execute(text("PRAGMA foreign_keys = OFF;"))


async def _enable_fk(db_session: AsyncSession):
    await db_session.execute(text("PRAGMA foreign_keys = ON;"))


# -------------------------
# Car inventory history
# -------------------------

async def test_get_car_inventory_history_404_when_empty(client, use_test_user):
    """
    When no history exists for the given car_inventory_id, the endpoint returns 404.
    """
    car_inventory_id = 43210
    response = await client.get(f"{CAR_HISTORY_URL}/{car_inventory_id}")
    assert response.status_code == 404
    assert "No bidding history found for this vehicle" in response.text


async def test_get_car_inventory_history_success(client, db_session: AsyncSession, test_user, use_test_user):
    """
    When history exists for the given car_inventory_id, the endpoint returns 200
    and includes a single item with expected fields.
    """
    car_inventory_id = 101

    await _disable_fk(db_session)
    db_session.add(
        HistoryModel(
            car_inventory_id=car_inventory_id,
            action="Inventory created",
            user_id=test_user.id,
            comment="initial comment",
            created_at=datetime.utcnow(),
        )
    )
    await db_session.commit()
    await _enable_fk(db_session)

    response = await client.get(f"{CAR_HISTORY_URL}/{car_inventory_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "history" in payload and isinstance(payload["history"], list)
    assert len(payload["history"]) == 1

    item = payload["history"][0]
    assert item["action"] == "Inventory created"
    assert item["comment"] == "initial comment"
    assert item["user"]["email"] == test_user.email


# -------------------------
# Part inventory history
# -------------------------

async def test_get_part_inventory_history_404_when_empty(client, use_test_user):
    """
    When no history exists for the given part_inventory_id, the endpoint returns 404.
    """
    part_inventory_id = 98765
    response = await client.get(f"{PART_HISTORY_URL}/{part_inventory_id}")
    assert response.status_code == 404
    assert "No bidding history found for this vehicle" in response.text


async def test_get_part_inventory_history_success(client, db_session: AsyncSession, test_user, use_test_user):
    """
    When history exists for the given part_inventory_id, the endpoint returns 200
    and includes a single item with expected fields.
    """
    part_inventory_id = 202

    await _disable_fk(db_session)
    db_session.add(
        HistoryModel(
            part_inventory_id=part_inventory_id,
            action="Part added",
            user_id=test_user.id,
            comment="part note",
            created_at=datetime.utcnow(),
        )
    )
    await db_session.commit()
    await _enable_fk(db_session)

    response = await client.get(f"{PART_HISTORY_URL}/{part_inventory_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "history" in payload and isinstance(payload["history"], list)
    assert len(payload["history"]) == 1

    item = payload["history"][0]
    assert item["action"] == "Part added"
    assert item["comment"] == "part note"
    assert item["user"]["email"] == test_user.email
