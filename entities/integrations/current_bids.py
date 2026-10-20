"""Adapt the existing retrying parsing call to the current-bid source port."""

from collections.abc import Callable, Sequence
from typing import Any

from domain.bid_refresh import BidTarget


class ParsingCurrentBidSource:
    def __init__(self, post: Callable[..., Any], auth_token: str) -> None:
        self._post = post
        self._auth_token = auth_token

    def fetch(self, targets: Sequence[BidTarget]) -> Any:
        response = self._post(
            url="/api/v1/parsers/scrape/current_bid",
            json={"items": [{"id": car.id, "source": car.auction, "lot": car.lot} for car in targets]},
            headers={"X-Auth-Token": self._auth_token},
            timeout=900.0,
        )
        return response.json()
