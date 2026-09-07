"""Keep source composition stable across partial valuation responses."""


def merge_market_prices(previous_price, previous_sources, incoming_sources):
    previous_sources = previous_sources or {}
    merged = {**previous_sources, **incoming_sources}
    # Legacy averages have no source breakdown: establish a complete baseline.
    if previous_price and not previous_sources and len(incoming_sources) < 3:
        return previous_price, previous_sources
    if not incoming_sources:
        return previous_price, previous_sources
    if previous_price and previous_sources and merged == previous_sources:
        return previous_price, previous_sources
    return int(sum(merged.values()) / len(merged)), merged
