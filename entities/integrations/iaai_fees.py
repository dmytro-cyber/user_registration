"""Map the existing image scraping call to the IAAI fee source interface."""

from collections.abc import Callable
from typing import Any

import httpx

from domain.iaai_fees import FeeImage, IaaiFeeResponse
from exceptions.fee_import import FeeSourceUnavailable


class ParsingIaaiFeeSource:
    def __init__(self, client_factory: Callable[..., Any]) -> None:
        self._client_factory = client_factory

    async def fetch(self, high_volume: FeeImage, internet_bid: FeeImage) -> IaaiFeeResponse:
        try:
            async with self._client_factory(timeout=60) as client:
                files = {
                    "high_volume": (high_volume.filename, high_volume.content, high_volume.content_type),
                    "internet_bid": (internet_bid.filename, internet_bid.content, internet_bid.content_type),
                }
                response = await client.post("/api/v1/parsers/scrape/iaai/fees", files=files)
            # The legacy endpoint consumes JSON error responses too; no raise_for_status.
            return IaaiFeeResponse(response.status_code, response.json())
        except httpx.RequestError as exc:
            raise FeeSourceUnavailable(str(exc)) from exc
