"""Bid calculations independent of HTTP, persistence and task execution."""

from collections.abc import Iterable


def calculate_auction_fee(bid_amount: float, fees: Iterable[tuple[float, bool]]) -> float:
    total = 0.0
    for amount, percent in fees:
        total += (float(amount) / 100.0) * bid_amount if percent else float(amount)
    return total


def calculate_profitability(
    market_price: float | None, total_investments: float,
) -> tuple[float | None, float | None]:
    """Return profit margin and ROI, retaining the existing float arithmetic."""
    if market_price is not None and total_investments > 0:
        profit_margin = float(market_price) - total_investments
        roi = (profit_margin / total_investments) * 100
        return profit_margin, roi
    return None, None
