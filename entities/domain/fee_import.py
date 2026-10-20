"""Copart tariff conversion without database, HTTP or worker dependencies."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

OPEN_PRICE_CAP = 1_000_000.0


@dataclass(frozen=True)
class FeeBand:
    fee_type: str
    amount: float
    percent: bool
    price_from: float
    price_to: float


class CopartFeeSource(Protocol):
    def fetch(self) -> Any: ...


def parse_amount(value: Any) -> tuple[float, bool]:
    if isinstance(value, dict):
        return float(value.get("amount", 0.0)), bool(value.get("percent", False))
    return float(value), False


def unwrap_range_section(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    labels = {"secured", "unsecured", "live_bid", "online", "onsite", "credit", "cash"}
    keys = list(value)
    if len(keys) == 1 and keys[0] in labels and isinstance(value[keys[0]], dict):
        return value[keys[0]]
    return value


def parse_price_range(key: str | None) -> tuple[float, float]:
    if not key:
        return 0.0, OPEN_PRICE_CAP
    value = key.strip()
    if not any(character.isdigit() for character in value):
        return 0.0, OPEN_PRICE_CAP
    if "-" in value:
        lower, upper = value.split("-", 1)
        lower = lower.replace("$", "").replace(",", "").replace("+", "").strip()
        upper = upper.replace("$", "").replace(",", "").replace("+", "").strip()
        return float(lower or 0.0), float(upper) if upper else OPEN_PRICE_CAP
    if value.endswith("+") or value.startswith("+"):
        lower = value.replace("$", "").replace(",", "").replace("+", "").strip()
        return float(lower or 0.0), OPEN_PRICE_CAP
    price = float(value.replace("$", "").replace(",", ""))
    return price, price


def copart_fee_bands(fees: dict) -> Iterator[FeeBand]:
    bidding = fees.get("bidding_fees", {}).get("secured", {})
    bidding_ranges = unwrap_range_section(unwrap_range_section(bidding))
    yield from _range_bands("bidding_fees", bidding_ranges)
    virtual = fees.get("virtual_bid_fee", {}).get("live_bid", {})
    virtual_ranges = unwrap_range_section(virtual)
    yield from _range_bands("virtual_bid_fee", virtual_ranges)
    for fee_type in ("gate_fee", "environmental_fee"):
        fixed = fees.get(fee_type, {})
        if isinstance(fixed, dict) and "amount" in fixed:
            yield FeeBand(fee_type, float(fixed["amount"]), False, 0.0, OPEN_PRICE_CAP)


def _range_bands(fee_type: str, ranges: dict) -> Iterator[FeeBand]:
    for key, value in ranges.items():
        amount, percent = parse_amount(value)
        lower, upper = parse_price_range(key)
        yield FeeBand(fee_type, amount, percent, lower, upper)
