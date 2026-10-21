"""Celery adapters for the established enrichment and filter kickoff contracts."""

from typing import TYPE_CHECKING

from domain.filter_kickoff import KickoffPublication
from domain.vehicle_ingestion import EnrichmentTaskPayload

if TYPE_CHECKING:
    from celery import Celery


class CeleryVehicleEnrichmentPublisher:
    def __init__(self, client: "Celery") -> None:
        self._client = client

    def publish(self, payload: EnrichmentTaskPayload) -> str | None:
        result = self._client.send_task(
            "tasks.task.parse_and_update_car",
            kwargs=payload,
            queue="car_parsing_queue",
        )
        return getattr(result, "id", None)


class CeleryFilterKickoffPublisher:
    def __init__(self, client: "Celery") -> None:
        self._client = client

    def publish(self, filter_id: int, queue_item_id: int) -> KickoffPublication:
        return self._client.send_task(
            "tasks.task.kickoff_parse_for_filter",
            kwargs={"filter_id": filter_id, "lock_token": str(queue_item_id)},
            queue="car_parsing_queue",
        )


class CeleryTaskVehicleEnrichmentPublisher:
    """Use the registered task's delay routing for filter execution."""

    def __init__(self, task) -> None:
        self._task = task

    def publish(self, payload: EnrichmentTaskPayload) -> str | None:
        self._task.delay(**payload)
        return None


class CeleryFilterUpdateKickoffPublisher:
    """Retain PATCH's direct kickoff with a numeric filter-id lock token."""

    def __init__(self, client: "Celery") -> None:
        self._client = client

    def publish(self, filter_id: int) -> None:
        self._client.send_task(
            "tasks.task.kickoff_parse_for_filter",
            kwargs={"filter_id": filter_id, "lock_token": filter_id},
            queue="car_parsing_queue",
        )
