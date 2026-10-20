import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from integrations import scraping_operations


@pytest.mark.asyncio
async def test_iaai_worker_decodes_files_selects_live_bid_and_cleans_up(monkeypatch):
    from integrations.fees import iaai_image

    paths = []

    def parse(path, target_fee_header=None):
        paths.append(path)
        assert Path(path).read_bytes() == (b"internet" if target_fee_header else b"high-volume")
        return {"0.00-99.99": 5 if target_fee_header == "LIVE BID" else 25}

    monkeypatch.setattr(iaai_image, "parse_fee_table", parse)
    response = await scraping_operations.execute_operation(
        "iaai_fees",
        {
            "high_volume": {"filename": "high.png", "content": base64.b64encode(b"high-volume").decode()},
            "internet_bid": {"filename": "internet.svg", "content": base64.b64encode(b"internet").decode()},
        },
    )
    assert response["status"] == 200
    fees = response["body"]["fees"]
    assert fees["high_volume_buyer_fees"]["fees"] == {"0.00-99.99": 25}
    assert fees["internet_bid_buyer_fees"]["fees"] == {"0.00-99.99": 5}
    assert fees["service_fee"]["amount"] == 95.0
    assert fees["environmental_fee"]["amount"] == 15.0
    assert fees["title_handling_fee"]["amount"] == 20.0
    assert all(not Path(path).exists() for path in paths)


@pytest.mark.asyncio
async def test_dealercenter_worker_preserves_bounded_retries_and_error_response(monkeypatch):
    pytest.importorskip("playwright")
    pytest.importorskip("bs4")
    from integrations.dealercenter import scraper

    calls = []

    class FailingScraper:
        def __init__(self, **kwargs):
            calls.append(kwargs["vin"])

        async def get_history_and_market_data_async(self):
            raise ValueError("upstream failed")

    monkeypatch.setenv("PARSING_ENVIRON", "prod")
    monkeypatch.setattr(scraper, "DealerCenterScraper", FailingScraper)
    sleep = AsyncMock()
    monkeypatch.setattr(scraping_operations, "asyncio", SimpleNamespace(sleep=sleep))
    response = await scraping_operations.execute_operation(
        "dealercenter",
        {
            "car_vin": "VIN",
            "car_make": "Toyota",
            "car_model": "Camry",
            "car_year": 2018,
            "car_transmison": "Automatic",
        },
    )
    assert calls == ["VIN", "VIN", "VIN"]
    assert sleep.await_count == 2
    assert response["status"] == 200
    assert response["body"]["error"] == "upstream failed"
    assert response["body"]["owners"] is None
