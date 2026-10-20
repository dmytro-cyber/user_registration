from unittest.mock import MagicMock, Mock

import pytest

from integrations.copart_fees import ParsingCopartFeeSource


def test_source_preserves_request_and_closes_client():
    payload = {"fees": {"copart": {"fees": {}}}}
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    response = client.get.return_value
    response.json.return_value = payload
    assert ParsingCopartFeeSource(factory).fetch() == payload
    factory.assert_called_once_with(timeout=60)
    client.get.assert_called_once_with("/api/v1/parsers/scrape/fees")
    response.raise_for_status.assert_called_once()
    factory.return_value.__exit__.assert_called_once()


def test_http_failure_propagates_before_payload_is_read():
    factory = MagicMock()
    response = factory.return_value.__enter__.return_value.get.return_value
    response.raise_for_status = Mock(side_effect=RuntimeError("upstream failed"))
    with pytest.raises(RuntimeError, match="upstream failed"):
        ParsingCopartFeeSource(factory).fetch()
    response.json.assert_not_called()
    factory.return_value.__exit__.assert_called_once()
