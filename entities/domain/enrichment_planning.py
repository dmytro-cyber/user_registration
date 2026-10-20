"""Pure preparation of parser facts, market plans and enrichment projections."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from domain.market_price import merge_market_prices


class FeeCharge(Protocol):
    percent: bool
    amount: float


class RoiConfiguration(Protocol):
    roi: float
    profit_margin: float


@dataclass(frozen=True)
class MarketRefreshPlan:
    previous_price: int | None
    previous_sources: dict[str, int] | None
    incoming_sources: dict[str, int]
    candidate_price: int | None
    merged_sources: dict[str, int]

    @property
    def should_refresh(self) -> bool:
        return self.candidate_price is not None and self.candidate_price != self.previous_price

    @property
    def calculation_price(self) -> int | None:
        return self.candidate_price if self.should_refresh else self.previous_price

    @property
    def is_complete(self) -> bool:
        return len(self.incoming_sources) == 3


@dataclass(frozen=True)
class EnrichmentPreparation:
    owners: int | None
    mileage: int | None
    accident_count: int | None
    market: MarketRefreshPlan


@dataclass(frozen=True)
class RoiProjection:
    total_investments: float = 0.0
    profit_margin_percent: float = 0.0
    profit_margin: float = 0.0
    roi: float = 0.0


@dataclass(frozen=True)
class EnrichmentCompletion:
    preparation: EnrichmentPreparation
    projection: RoiProjection
    auction_fee: float
    expected_mileage: int | None
    business_attempts: int
    sales_history_rows: list[dict[str, Any]]
    sales_history_reason: str | None
    screenshot_url: str | None


def calculate_roi_projection(market_price: int | None, configuration: RoiConfiguration | None) -> RoiProjection:
    if configuration and market_price:
        return RoiProjection(
            market_price / (1 + configuration.roi / 100.0),
            configuration.profit_margin,
            market_price * (configuration.profit_margin / 100.0),
            configuration.roi,
        )
    return RoiProjection()


def normalize_mileage(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace(",", "").strip()
        if not v:
            return None
    try:
        return int(v)
    except Exception:
        return None


def parse_int_safe(value: Any) -> Optional[int]:
    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        cleaned = cleaned.replace(",", "").replace(" ", "")
        match = re.search(r"-?\d+", cleaned)
        if match:
            try:
                return int(match.group(0))
            except Exception:
                return None

    return None


def normalize_owners(value: Any) -> Optional[int]:
    parsed = parse_int_safe(value)
    if parsed is None:
        return None
    return parsed if parsed >= 0 else None


def normalize_accident_count(value: Any) -> Optional[int]:
    parsed = parse_int_safe(value)
    if parsed is None:
        return None
    return parsed if parsed >= 0 else None


def normalize_mileage_value(value: Any) -> Optional[int]:
    try:
        normalized = normalize_mileage(value)
        if normalized is None:
            return None
        return int(normalized)
    except Exception:
        return None


def apply_enrichment_fees(investment: float, fees: Iterable[FeeCharge]) -> float:
    fee_total = 0.0
    for fee in fees:
        if fee.percent:
            fee_total += (fee.amount / 100.0) * investment
        else:
            fee_total += float(fee.amount)
    return fee_total


def prepare_enrichment(
    data: Mapping[str, Any],
    previous_price: int | None,
    previous_sources: dict[str, int] | None,
) -> EnrichmentPreparation:
    owners = normalize_owners(data.get("owners"))
    mileage = normalize_mileage_value(data.get("mileage"))
    accident_count = normalize_accident_count(data.get("accident_count"))
    incoming_sources = {}
    for key in ("jd", "d_max", "manheim"):
        price = parse_int_safe(data.get(key))
        if price is not None and price > 0:
            incoming_sources[key] = price
    candidate_price, merged_sources = merge_market_prices(previous_price, previous_sources, incoming_sources)
    return EnrichmentPreparation(
        owners,
        mileage,
        accident_count,
        MarketRefreshPlan(previous_price, previous_sources, incoming_sources, candidate_price, merged_sources),
    )
