"""Enrichment reason formatting and parser-failure result contract."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

from domain.vehicle_ingestion import EnrichmentTaskPayload

if TYPE_CHECKING:
    from schemas.vehicle import CarCreateSchema


class ParserFailureResult(TypedDict):
    status: Literal["exception", "not_found", "locked_skip"]
    vin: str


def append_enrichment_reason(current: str | None, text: str) -> str:
    if not text:
        return current or ""
    reason = text if text.endswith(";") else f"{text};"
    current = current or ""
    if reason not in current:
        current += reason
    return current


def remove_enrichment_reason(current: str | None, text: str) -> str:
    if not text or not current:
        return current or ""
    reason = text if text.endswith(";") else f"{text};"
    current = current.replace(reason, "")
    return current.strip()


def is_lock_conflict_error(exc: Exception) -> bool:
    text = str(exc).lower()

    markers = (
        "could not obtain lock on row",
        "lock timeout",
        "canceling statement due to lock timeout",
        "could not obtain lock",
        "nowait",
    )
    return any(marker in text for marker in markers)


@dataclass(frozen=True)
class EnrichmentSnapshot:
    business_attempts: int
    auction: str | None
    market_price: int | None
    market_sources: dict[str, int] | None


class VehicleEnrichmentSource(Protocol):
    def fetch_history(self, vin: str) -> "CarCreateSchema | None": ...
    def parse(self, request: EnrichmentTaskPayload) -> Any: ...


class VehicleEnrichmentReportStorage(Protocol):
    def save(self, vin: str, html_data: str) -> str: ...
