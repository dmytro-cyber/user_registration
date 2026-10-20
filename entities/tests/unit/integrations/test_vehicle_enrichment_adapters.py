from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest

from exceptions.vehicle_enrichment import EnrichmentParserError
from integrations.vehicle_enrichment import ParsingVehicleEnrichmentSource, S3EnrichmentReportStorage


def request(**changes):
    return {
        "vin": "VIN",
        "car_name": None,
        "car_engine": None,
        "mileage": None,
        "car_make": None,
        "car_model": None,
        "car_year": None,
        "car_transmison": None,
        **changes,
    }


def response(status=200, **kwargs):
    return httpx.Response(status, request=httpx.Request("GET", "http://local/parser"), **kwargs)


def test_parser_retains_raw_query_spelling_timeout_token_and_payload():
    payload = {"jd": 12345, "error": "", "extra": {"unchanged": True}}
    get = Mock(return_value=response(json=payload))
    source = ParsingVehicleEnrichmentSource(get, "test-token")
    assert (
        source.parse(
            request(
                vin="VIN/?",
                car_name="Name & Other",
                car_engine="2.0 V6",
                mileage=0,
                car_make="Ford",
                car_model="F/150",
                car_year=0,
                car_transmison="A/T",
            )
        )
        == payload
    )
    get.assert_called_once_with(
        "/api/v1/parsers/scrape/dc?car_vin=VIN/?&car_mileage=0&car_name=Name & Other&car_engine=2.0 V6"
        "&car_make=Ford&car_model=F/150&car_year=0&car_transmison=A/T&only_history=false",
        headers={"X-Auth-Token": "test-token"},
        timeout=300.0,
    )


def test_parser_optional_arguments_remain_empty_strings():
    get = Mock(return_value=response(json={}))
    assert ParsingVehicleEnrichmentSource(get, "token").parse(request()) == {}
    assert get.call_args.args[0] == (
        "/api/v1/parsers/scrape/dc?car_vin=VIN&car_mileage=&car_name=&car_engine=&car_make=&car_model="
        "&car_year=&car_transmison=&only_history=false"
    )


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("request", "upstream parser error;"),
        ("status", "upstream parser error;"),
        ("json", "invalid parser json response;"),
        ("payload", "scraping error: bad VIN;"),
    ],
)
def test_parser_error_reason_mapping(kind, reason):
    get = Mock()
    if kind == "request":
        get.side_effect = RuntimeError("offline")
    else:
        get.return_value = {
            "status": response(500),
            "json": response(content=b"not JSON"),
            "payload": response(json={"error": "  bad VIN  "}),
        }[kind]
    with pytest.raises(EnrichmentParserError) as error:
        ParsingVehicleEnrichmentSource(get, "token").parse(request())
    assert error.value.reason == reason


@pytest.mark.parametrize("payload", [None, [], "text", 7])
def test_non_object_json_still_propagates_without_mapping_to_parser_failure(payload):
    get = Mock(return_value=response(content=b"null") if payload is None else response(json=payload))
    with pytest.raises(AttributeError):
        ParsingVehicleEnrichmentSource(get, "token").parse(request())


def test_history_retains_request_validation_and_raw_source_value():
    get = Mock(return_value=response(json={"vin": "VIN", "sales_history": [{"source": "", "final_bid": "123"}]}))
    snapshot = ParsingVehicleEnrichmentSource(get, "token").fetch_history("VIN")
    assert snapshot.sales_history[0].source == "" and snapshot.sales_history[0].final_bid == 123
    get.assert_called_once_with("/api/v1/apicar/get/VIN", headers={"X-Auth-Token": "token"}, timeout=30.0)


def test_null_history_is_distinct_from_invalid_snapshot():
    get = Mock(return_value=response(content=b"null"))
    assert ParsingVehicleEnrichmentSource(get, "token").fetch_history("VIN") is None


@pytest.mark.parametrize("kind", ["request", "status", "json", "schema"])
def test_history_errors_propagate_for_caller_best_effort_policy(kind):
    get = Mock()
    if kind == "request":
        get.side_effect = RuntimeError("offline")
    else:
        get.return_value = {
            "status": response(500),
            "json": response(content=b"invalid"),
            "schema": response(json={"sales_history": []}),
        }[kind]
    with pytest.raises(Exception) as error:
        ParsingVehicleEnrichmentSource(get, "token").fetch_history("VIN")
    assert not isinstance(error.value, EnrichmentParserError)


def storage(factory, endpoint="http://local"):
    return S3EnrichmentReportStorage(
        factory,
        endpoint_url=endpoint,
        access_key="test-access",
        secret_key="test-secret",
        bucket_name="reports",
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5),
    )


def test_report_retains_key_utf8_bytes_factory_and_literal_url():
    client = Mock()
    captured = []
    client.upload_fileobj_sync.side_effect = lambda key, stream: captured.append((key, stream.read()))
    factory = Mock(return_value=client)
    url = storage(factory, endpoint="http://local/").save("VIN", "<html>Звіт</html>")
    assert captured == [("auto_checks/VIN/20260102_030405_report.html", "<html>Звіт</html>".encode("utf-8"))]
    assert url == "http://local//reports/auto_checks/VIN/20260102_030405_report.html"
    factory.assert_called_once_with(
        endpoint_url="http://local/", access_key="test-access", secret_key="test-secret", bucket_name="reports"
    )


@pytest.mark.parametrize("initialize", [True, False])
def test_report_failures_propagate_for_caller_best_effort_policy(initialize):
    factory = Mock(side_effect=RuntimeError("initialize failed")) if initialize else Mock()
    if not initialize:
        factory.return_value.upload_fileobj_sync.side_effect = RuntimeError("upload failed")
    with pytest.raises(RuntimeError):
        storage(factory).save("VIN", "report")


def test_invalid_report_type_still_fails_after_client_creation():
    factory = Mock()
    with pytest.raises(AttributeError):
        storage(factory).save("VIN", 123)
    factory.assert_called_once()
    factory.return_value.upload_fileobj_sync.assert_not_called()
