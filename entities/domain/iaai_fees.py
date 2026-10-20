"""IAAI image fee import boundary and its existing tolerant conversion rules."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from domain.fee_import import OPEN_PRICE_CAP, FeeBand


@dataclass(frozen=True)
class FeeImage:
    filename: str | None
    content: bytes
    content_type: str | None


@dataclass(frozen=True)
class IaaiFeeResponse:
    status: int
    data: Any


class IaaiFeeSource(Protocol):
    async def fetch(self, high_volume: FeeImage, internet_bid: FeeImage) -> IaaiFeeResponse: ...


def try_parse_amount(raw: Any) -> tuple[float, bool] | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw), False
    value = str(raw).strip()
    if not value or value.lower() == "none":
        return None
    if re.fullmatch(r"(free|no\s*fee)", value, re.I):
        return 0.0, False
    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
    if percent:
        return float(percent.group(1)), True
    try:
        return float(value.replace("$", "").replace(",", "")), False
    except ValueError:
        return None


def parse_price_range(key: str) -> tuple[float, float]:
    value = (key or "").strip()
    if "-" in value:
        lower, upper = value.split("-", 1)
        return float(lower), float(upper)
    return float(value.replace("+", "")), OPEN_PRICE_CAP


def iaai_fee_bands(data: Any) -> Iterator[FeeBand]:
    # Resolve sections before persistence begins, preserving the old failure order.
    fees = data.get("fees", {})
    high_volume = fees.get("high_volume_buyer_fees", {"fees": {}}).get("fees", {}) or {}
    internet_bid = fees.get("internet_bid_buyer_fees", {"fees": {}}).get("fees", {}) or {}
    fixed = (
        ("service_fee", fees.get("service_fee", {"amount": 95.0})),
        ("environmental_fee", fees.get("environmental_fee", {"amount": 15.0})),
        ("title_handling_fee", fees.get("title_handling_fee", {"amount": 20.0})),
    )
    return _fee_bands(fixed, (("high_volume_buyer_fees", high_volume), ("internet_bid_buyer_fees", internet_bid)))


def _fee_bands(
    fixed: tuple[tuple[str, Any], ...],
    sections: tuple[tuple[str, Any], ...],
) -> Iterator[FeeBand]:
    for fee_type, payload in fixed:
        parsed = try_parse_amount(payload.get("amount"))
        if parsed is not None:
            amount, percent = parsed
            yield FeeBand(fee_type, amount, percent, 0.0, OPEN_PRICE_CAP)
    for fee_type, ranges in sections:
        for key, raw in (ranges or {}).items():
            parsed = try_parse_amount(raw)
            if parsed is None:
                continue
            try:
                lower, upper = parse_price_range(key)
            except Exception:
                continue
            amount, percent = parsed
            yield FeeBand(fee_type, amount, percent, lower, upper)
