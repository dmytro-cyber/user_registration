from services.market_price import merge_market_prices


def test_partial_refresh_keeps_missing_sources():
    sources = {"jd": 18000, "d_max": 21000, "manheim": 24000}
    price, updated = merge_market_prices(21000, sources, {"jd": 15000})
    assert price == 20000
    assert updated == {"jd": 15000, "d_max": 21000, "manheim": 24000}
    assert sources["jd"] == 18000


def test_same_partial_response_does_not_change_average():
    sources = {"jd": 18000, "d_max": 21000, "manheim": 24000}
    assert merge_market_prices(21000, sources, {"jd": 18000}) == (21000, sources)


def test_zero_legacy_price_can_recover_with_partial_response():
    assert merge_market_prices(0, None, {"jd": 15000}) == (15000, {"jd": 15000})


def test_legacy_average_is_preserved_without_source_baseline():
    assert merge_market_prices(20000, None, {"jd": 15000}) == (20000, {})


def test_empty_response_preserves_price():
    assert merge_market_prices(20000, None, {}) == (20000, {})


def test_unchanged_sources_preserve_manual_valuation():
    sources = {"jd": 18000, "d_max": 21000, "manheim": 24000}
    assert merge_market_prices(23000, sources, sources) == (23000, sources)
