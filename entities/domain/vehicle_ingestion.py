"""Internal import plans and queue boundary; these are not HTTP schemas."""

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict


class EnrichmentTaskPayload(TypedDict):
    vin: str
    car_name: str | None
    car_engine: str | None
    mileage: int | None
    car_make: str | None
    car_model: str | None
    car_year: int | None
    car_transmison: str | None


class VehicleEnrichmentPublisher(Protocol):
    def publish(self, payload: EnrichmentTaskPayload) -> str | None: ...


@dataclass(frozen=True)
class VehicleSnapshotSaveResult:
    saved: bool
    needs_enrichment: bool = False


class VehicleBatchResult(TypedDict):
    celery_tasks: list[EnrichmentTaskPayload]
    total: int
    new_count: int
    updated_count: int


@dataclass
class VehicleBatchPlan:
    car_rows: list[dict[str, Any]]
    conditions: list[dict[str, Any]]
    photos: list[dict[str, Any]]
    enrichment_tasks: list[EnrichmentTaskPayload]
    new_count: int
    updated_count: int

    def result(self) -> VehicleBatchResult:
        return {
            "celery_tasks": self.enrichment_tasks,
            "total": len(self.car_rows),
            "new_count": self.new_count,
            "updated_count": self.updated_count,
        }
