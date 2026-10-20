"""Execute queued filters with separate read and completion sessions."""

from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from crud.filter_execution import finish_filter_kickoff, load_filter_vehicles
from domain.vehicle_ingestion import VehicleEnrichmentPublisher
from models.filter_kickoff_queue import FilterKickoffQueueStatus


def execute_filter_kickoff(
    sessions: Callable[[], Session],
    publisher: VehicleEnrichmentPublisher,
    filter_id: int,
    lock_token: str,
    batch_size: int = 100,
) -> dict:
    queue_item_id = int(lock_token)

    try:
        with sessions() as session:
            result = load_filter_vehicles(session, filter_id)
            if result is None:
                if finish_filter_kickoff(
                    session,
                    queue_item_id,
                    FilterKickoffQueueStatus.FAILED,
                    datetime.now(timezone.utc),
                    "filter_not_found",
                ):
                    session.commit()

                return {
                    "status": "error",
                    "reason": "filter_not_found",
                    "filter_id": filter_id,
                }

        batch = []
        enqueued = 0

        for row in result:
            batch.append(
                {
                    "vin": row["vin"],
                    "vehicle": row["vehicle"],
                    "engine_title": row["engine_title"],
                    "mileage": row["mileage"],
                    "make": row["make"],
                    "model": row["model"],
                    "year": row["year"],
                    "transmision": row["transmision"],
                }
            )

            if len(batch) >= batch_size:
                for v in batch:
                    publisher.publish(
                        {
                            "vin": v["vin"],
                            "car_name": v["vehicle"],
                            "car_engine": v["engine_title"],
                            "mileage": v["mileage"],
                            "car_make": v["make"],
                            "car_model": v["model"],
                            "car_year": v["year"],
                            "car_transmison": v["transmision"],
                        }
                    )

                enqueued += len(batch)
                batch.clear()

        if batch:
            for v in batch:
                publisher.publish(
                    {
                        "vin": v["vin"],
                        "car_name": v["vehicle"],
                        "car_engine": v["engine_title"],
                        "mileage": v["mileage"],
                        "car_make": v["make"],
                        "car_model": v["model"],
                        "car_year": v["year"],
                        "car_transmison": v["transmision"],
                    }
                )

            enqueued += len(batch)

        with sessions() as session:
            if finish_filter_kickoff(
                session, queue_item_id, FilterKickoffQueueStatus.DONE, datetime.now(timezone.utc), None
            ):
                session.commit()

        return {
            "status": "ok",
            "filter_id": filter_id,
            "enqueued": enqueued,
        }

    except Exception as exc:
        with sessions() as session:
            if finish_filter_kickoff(
                session, queue_item_id, FilterKickoffQueueStatus.FAILED, datetime.now(timezone.utc), str(exc)
            ):
                session.commit()
        raise
