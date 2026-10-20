"""Adapt the existing serialized scraping call to the Copart fee source."""

from collections.abc import Callable
from typing import Any


class ParsingCopartFeeSource:
    def __init__(self, client_factory: Callable[..., Any]) -> None:
        self._client_factory = client_factory

    def fetch(self) -> Any:
        with self._client_factory(timeout=60) as client:
            response = client.get("/api/v1/parsers/scrape/fees")
            response.raise_for_status()
            return response.json()
