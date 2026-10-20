from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from domain.iaai_fees import FeeImage, IaaiFeeResponse
from exceptions.fee_import import FeeSourceUnavailable
from integrations.iaai_fees import ParsingIaaiFeeSource


@pytest.mark.asyncio
async def test_source_preserves_file_bytes_and_error_response_status():
    factory = MagicMock()
    client = factory.return_value.__aenter__.return_value
    response = httpx.Response(400, json={"detail": "bad image"})
    client.post = AsyncMock(return_value=response)
    high = FeeImage("high.png", b"high-content", "image/png")
    internet = FeeImage("internet.svg", b"svg-content", "image/svg+xml")
    assert await ParsingIaaiFeeSource(factory).fetch(high, internet) == IaaiFeeResponse(400, {"detail": "bad image"})
    factory.assert_called_once_with(timeout=60)
    client.post.assert_awaited_once_with(
        "/api/v1/parsers/scrape/iaai/fees",
        files={
            "high_volume": ("high.png", b"high-content", "image/png"),
            "internet_bid": ("internet.svg", b"svg-content", "image/svg+xml"),
        },
    )
    factory.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_error_becomes_typed_source_error_without_changing_text():
    factory = MagicMock()
    client = factory.return_value.__aenter__.return_value
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("source timeout"))
    image = FeeImage("image.png", b"content", "image/png")
    with pytest.raises(FeeSourceUnavailable, match="^source timeout$"):
        await ParsingIaaiFeeSource(factory).fetch(image, image)


@pytest.mark.asyncio
async def test_non_json_response_keeps_payload_error_class():
    factory = MagicMock()
    client = factory.return_value.__aenter__.return_value
    client.post = AsyncMock(return_value=httpx.Response(500, text="Internal Server Error"))
    image = FeeImage("image.png", b"content", "image/png")
    with pytest.raises(ValueError):
        await ParsingIaaiFeeSource(factory).fetch(image, image)
