from unittest.mock import Mock

import httpx
import pytest

from exceptions.expired_vehicles import ExpiredVehicleSourceError
from integrations.expired_vehicles import ParsingExpiredVehicleSource


def response(status, **kwargs):
    return httpx.Response(status, request=httpx.Request("GET", "http://parsers:8001/api/v1/apicar/VIN"), **kwargs)


@pytest.mark.parametrize("status", [200, 201])
def test_valid_snapshot_retains_path_timeout_and_validation(status):
    client = Mock()
    client.get.return_value = response(status, json={"vin": "VIN", "year": "2020"})
    factory = Mock(return_value=client)
    snapshot = ParsingExpiredVehicleSource(factory).fetch("VIN")
    assert snapshot.vin == "VIN" and snapshot.year == 2020
    factory.assert_called_once_with(timeout=30)
    client.get.assert_called_once_with("/api/v1/apicar/VIN", timeout=30.0)


def test_404_does_not_parse_body():
    client = Mock()
    client.get.return_value = response(404, content=b"not json")
    assert ParsingExpiredVehicleSource(Mock(return_value=client)).fetch("VIN") is None


@pytest.mark.parametrize(
    "kind,stage",
    [
        ("request", "HTTP request failed"),
        ("status", "parser returned HTTP error"),
        ("json", "payload validation failed"),
        ("schema", "payload validation failed"),
    ],
)
def test_recoverable_error_boundaries(kind, stage):
    client = Mock()
    if kind == "request":
        client.get.side_effect = RuntimeError("network")
    else:
        client.get.return_value = {
            "status": response(500),
            "json": response(200, content=b"invalid"),
            "schema": response(200, json={"year": 2020}),
        }[kind]
    with pytest.raises(ExpiredVehicleSourceError) as error:
        ParsingExpiredVehicleSource(Mock(return_value=client)).fetch("VIN")
    assert error.value.stage == stage and error.value.__cause__ is not None


def test_non_http_status_exception_still_propagates():
    client = Mock()
    client.get.return_value.status_code = 200
    client.get.return_value.raise_for_status.side_effect = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        ParsingExpiredVehicleSource(Mock(return_value=client)).fetch("VIN")
