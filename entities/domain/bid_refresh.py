"""Current-bid feed boundary and pure parsing/profit calculations."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict


@dataclass(frozen=True)
class BidTarget:
    id: int
    lot: int
    auction: str


class CurrentBidSource(Protocol):
    def fetch(self, targets: Sequence[BidTarget]) -> Any: ...


class BidRefreshResult(TypedDict):
    status: str
    updated_cars: int


@dataclass(frozen=True)
class AuctionBid:
    lot: int
    auction: str
    amount: Any


def normalize_auction(value: object) -> str:
    name = str(value or "").strip().lower()
    if name in {"1", "copart", "copart.com"}:
        return "copart"
    if name in {"2", "iaai", "iaa", "iaai.com"}:
        return "iaai"
    return name


def read_auction_bid(item: dict[str, Any]) -> AuctionBid | None:
    lot = str(item.get("lot_id") or "").split("-")[0].strip()
    if not lot.isdigit() or item.get("pre_bid") is None:
        return None
    return AuctionBid(int(lot), normalize_auction(item.get("site")), item.get("pre_bid"))


@dataclass(frozen=True)
class BidProfit:
    margin: float
    roi: float


def profit_from_current_bid(
    investment: float | None,
    current_bid: int,
    average_price: float | None,
) -> BidProfit | None:
    base = (investment or 0) + (current_bid or 0)
    if average_price is None or base <= 0:
        return None
    margin = float(average_price) - float(base)
    return BidProfit(margin, (margin / base) * 100.0)
