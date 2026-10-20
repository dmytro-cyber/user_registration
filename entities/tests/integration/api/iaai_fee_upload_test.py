from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from core.dependencies import get_iaai_fee_source
from domain.iaai_fees import FeeImage, IaaiFeeResponse
from exceptions.fee_import import FeeSourceUnavailable
from main import app
from models.vehicle import FeeModel

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/admin/upload-iaai-fees"
FILES = {
    "high_volume": ("high.png", b"high-content", "image/png"),
    "internet_bid": ("internet.svg", b"svg-content", "image/svg+xml"),
}


@pytest.fixture
def fee_source():
    source = AsyncMock()
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_iaai_fee_source] = lambda: source
    try:
        yield source
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def seed_fees(db):
    db.add_all([FeeModel(auction=auction, fee_type="old", amount=1, percent=False) for auction in ("iaai", "copart")])
    await db.commit()


async def assert_old_fees(db):
    rows = (await db.execute(select(FeeModel))).scalars().all()
    assert {(row.auction, row.fee_type, row.amount) for row in rows} == {("iaai", "old", 1), ("copart", "old", 1)}


async def test_upload_preserves_body_files_and_persisted_fee_rows(client, db_session, fee_source):
    await seed_fees(db_session)
    data = {
        "fees": {
            "service_fee": {"amount": "$100.50"},
            "environmental_fee": {"amount": "NO FEE"},
            "high_volume_buyer_fees": {"fees": {"0-100": "6% of sale price", "100+": "FREE", "label": "$10"}},
            "internet_bid_buyer_fees": {"fees": {"500": "$20", "bad": "invalid"}},
        }
    }
    fee_source.fetch.return_value = IaaiFeeResponse(200, data)
    response = await client.post(PATH, files=FILES)
    assert response.status_code == 200
    assert response.json() == {"message": "Forwarded successfully", "external_status": 200, "response": data}
    fee_source.fetch.assert_awaited_once_with(
        FeeImage("high.png", b"high-content", "image/png"), FeeImage("internet.svg", b"svg-content", "image/svg+xml")
    )
    rows = (await db_session.execute(select(FeeModel))).scalars().all()
    assert {
        (row.fee_type, row.amount, row.percent, row.price_from, row.price_to) for row in rows if row.auction == "iaai"
    } == {
        ("service_fee", 100.5, False, 0, 1000000),
        ("environmental_fee", 0, False, 0, 1000000),
        ("title_handling_fee", 20, False, 0, 1000000),
        ("high_volume_buyer_fees", 6, True, 0, 100),
        ("high_volume_buyer_fees", 0, False, 100, 1000000),
        ("internet_bid_buyer_fees", 20, False, 500, 1000000),
    }
    assert [(row.fee_type, row.amount) for row in rows if row.auction == "copart"] == [("old", 1)]


@pytest.mark.parametrize("status", [400, 500])
async def test_upstream_json_error_status_still_commits_defaults(client, db_session, fee_source, status):
    await seed_fees(db_session)
    data = {"detail": "upstream failed"}
    fee_source.fetch.return_value = IaaiFeeResponse(status, data)
    response = await client.post(PATH, files=FILES)
    assert response.status_code == status
    assert response.json() == {"message": "Forwarded successfully", "external_status": status, "response": data}
    rows = (await db_session.execute(select(FeeModel).where(FeeModel.auction == "iaai"))).scalars().all()
    assert {(row.fee_type, row.amount) for row in rows} == {
        ("service_fee", 95),
        ("environmental_fee", 15),
        ("title_handling_fee", 20),
    }


@pytest.mark.parametrize("failure,expected", [("source", 502), ("payload", 500), ("negative", 500), ("commit", 500)])
async def test_upload_error_mapping_and_rollback(client, db_session, fee_source, monkeypatch, failure, expected):
    await seed_fees(db_session)
    fee_source.fetch.return_value = IaaiFeeResponse(200, {})
    if failure == "source":
        fee_source.fetch.side_effect = FeeSourceUnavailable("source timeout")
    elif failure == "payload":
        fee_source.fetch.return_value = IaaiFeeResponse(200, {"fees": None})
    elif failure == "negative":
        fee_source.fetch.return_value = IaaiFeeResponse(200, {"fees": {"service_fee": {"amount": -1}}})
    elif failure == "commit":
        monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
    response = await client.post(PATH, files=FILES)
    assert response.status_code == expected
    assert response.json() == {
        "detail": "Failed to contact external service: source timeout"
        if failure == "source"
        else "Internal server error"
    }
    await assert_old_fees(db_session)
