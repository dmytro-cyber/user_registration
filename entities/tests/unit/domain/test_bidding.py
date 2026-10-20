"""Pure bid calculations require no application or database fixtures."""

import pytest

from domain.bidding import calculate_auction_fee, calculate_profitability


@pytest.mark.parametrize("bid,fees,expected", [
    (5000, [], 0.0),
    (5000, [(100, False), (10, True)], 600.0),
    (0, [(100, False), (10, True)], 100.0),
    (1234.56, [(2.5, True), (15.75, False)], (2.5 / 100.0) * 1234.56 + 15.75),
])
def test_auction_fee(bid, fees, expected):
    assert calculate_auction_fee(bid, iter(fees)) == expected


@pytest.mark.parametrize("market_price,investment,expected", [
    (12000, 6000, (6000.0, 100.0)),
    (0, 1000, (-1000.0, -100.0)),
    (1000, 2000, (-1000.0, -50.0)),
    (None, 1000, (None, None)),
    (12000, 0, (None, None)),
    (12000, -1, (None, None)),
])
def test_profitability(market_price, investment, expected):
    assert calculate_profitability(market_price, investment) == expected
