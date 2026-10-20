"""External parser and report-storage adapters for vehicle enrichment."""

from collections.abc import Callable
from datetime import datetime
from io import BytesIO
from typing import Any

from domain.vehicle_ingestion import EnrichmentTaskPayload
from exceptions.vehicle_enrichment import EnrichmentParserError
from schemas.vehicle import CarCreateSchema


class ParsingVehicleEnrichmentSource:
    def __init__(self, get_with_retries: Callable[..., Any], token: str) -> None:
        self._get = get_with_retries
        self._token = token

    def fetch_history(self, vin: str) -> CarCreateSchema | None:
        response = self._get(f"/api/v1/apicar/get/{vin}", headers={"X-Auth-Token": self._token}, timeout=30.0)
        response.raise_for_status()
        payload = response.json()
        return None if payload is None else CarCreateSchema.model_validate(payload)

    def parse(self, request: EnrichmentTaskPayload) -> Any:
        # Preserve raw query interpolation, including the legacy spelling and falsy values.
        url = (
            "/api/v1/parsers/scrape/dc"
            f"?car_vin={request['vin']}"
            f"&car_mileage={request['mileage'] if request['mileage'] is not None else ''}"
            f"&car_name={request['car_name'] or ''}"
            f"&car_engine={request['car_engine'] or ''}"
            f"&car_make={request['car_make'] or ''}"
            f"&car_model={request['car_model'] or ''}"
            f"&car_year={request['car_year'] if request['car_year'] is not None else ''}"
            f"&car_transmison={request['car_transmison'] or ''}"
            "&only_history=false"
        )
        try:
            response = self._get(url, headers={"X-Auth-Token": self._token}, timeout=300.0)
            response.raise_for_status()
        except Exception as exc:
            raise EnrichmentParserError("upstream parser error;", str(exc)) from exc
        try:
            payload = response.json()
        except Exception as exc:
            raise EnrichmentParserError("invalid parser json response;", str(exc)) from exc
        # Non-object JSON still fails here, as it did before this adapter was extracted.
        if payload.get("error"):
            error_text = str(payload.get("error") or "").strip()
            raise EnrichmentParserError(f"scraping error: {error_text};", error_text)
        return payload


class S3EnrichmentReportStorage:
    def __init__(
        self,
        client_factory: Callable[..., Any],
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket_name: str,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self._factory = client_factory
        self._endpoint = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket_name
        self._clock = clock

    def save(self, vin: str, html_data: str) -> str:
        client = self._factory(
            endpoint_url=self._endpoint,
            access_key=self._access_key,
            secret_key=self._secret_key,
            bucket_name=self._bucket,
        )
        file_key = f"auto_checks/{vin}/{self._clock().strftime('%Y%m%d_%H%M%S')}_report.html"
        client.upload_fileobj_sync(file_key, BytesIO(html_data.encode("utf-8")))
        return f"{self._endpoint}/{self._bucket}/{file_key}"
