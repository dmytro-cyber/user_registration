from types import SimpleNamespace
from unittest.mock import Mock

from domain.bid_refresh import BidTarget
from integrations.current_bids import ParsingCurrentBidSource


def test_adapter_keeps_parser_request_contract_and_payload():
    payload = {"bids": [{"lot_id": "123-1", "site": 1, "pre_bid": 0}]}
    post = Mock(return_value=SimpleNamespace(json=lambda: payload))
    assert ParsingCurrentBidSource(post, "test-token").fetch([BidTarget(7, 123, "Copart")]) == payload
    post.assert_called_once_with(
        url="/api/v1/parsers/scrape/current_bid",
        json={"items": [{"id": 7, "source": "Copart", "lot": 123}]},
        headers={"X-Auth-Token": "test-token"},
        timeout=900.0,
    )
