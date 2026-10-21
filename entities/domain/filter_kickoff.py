"""Publication boundary for scheduled filter parsing jobs."""

from typing import Literal, Protocol, TypedDict


class KickoffPublication(Protocol):
    id: str


class FilterKickoffPublisher(Protocol):
    def publish(self, filter_id: int, queue_item_id: int) -> KickoffPublication: ...


class FilterDispatchResult(TypedDict):
    status: Literal["ok"]
    dispatched: int
    failed: int


class FilterUpdateKickoffPublisher(Protocol):
    def publish(self, filter_id: int) -> None: ...
