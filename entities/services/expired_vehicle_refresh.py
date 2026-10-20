"""Expired-auction workflow with the existing per-vehicle transactions."""

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crud.expired_vehicle_refresh import apply_expired_vehicle_snapshot, find_expired_vehicle, load_expired_active_vins
from domain.expired_vehicles import ExpiredVehicleSource, should_archive_expired_vehicle
from exceptions.expired_vehicles import ExpiredVehicleSourceError
from models.vehicle import RelevanceStatus
from services.expired_vehicles import cleanup_expired_irrelevant_vehicles

logger = logging.getLogger(__name__)


def refresh_expired_vehicles(
    sessions: Callable[[], Session], source: ExpiredVehicleSource, now_utc: datetime
) -> dict[str, int]:
    stats = {
        "checked": 0,
        "archived": 0,
        "reactivated_or_kept_active": 0,
        "deleted_irrelevant": 0,
        "updated": 0,
        "errors": 0,
    }

    with sessions() as db:
        stats.update(cleanup_expired_irrelevant_vehicles(db, now_utc))

    # -----------------------------------
    # 2. Process ACTIVE past cars
    # -----------------------------------
    with sessions() as db:
        vin_rows = load_expired_active_vins(db, now_utc)

        logger.info("Expired-auction: selected %d ACTIVE VINs to process", len(vin_rows))

        for vin in vin_rows:
            stats["checked"] += 1

            try:
                vehicle_info = source.fetch(vin)
            except ExpiredVehicleSourceError as exc:
                logger.exception("VIN %s: %s: %s", vin, exc.stage, exc)
                stats["errors"] += 1
                continue

            if vehicle_info is None:
                try:
                    car = find_expired_vehicle(db, vin)

                    if car:
                        car.relevance = RelevanceStatus.ARCHIVAL
                        car.is_manually_upserted = False
                        db.add(car)
                        db.commit()
                        stats["archived"] += 1
                        logger.info("VIN %s marked as ARCHIVAL after 404", vin)
                except Exception as e:
                    db.rollback()
                    logger.exception("VIN %s: failed to mark ARCHIVAL: %s", vin, e)
                    stats["errors"] += 1

                continue

            try:
                existing_vehicle = find_expired_vehicle(db, vin, with_snapshot=True)

                if not existing_vehicle:
                    logger.info("VIN %s: not found during update stage", vin)
                    continue

                apply_expired_vehicle_snapshot(db, existing_vehicle, vehicle_info)

                if should_archive_expired_vehicle(existing_vehicle.auction_name, existing_vehicle.date, now_utc):
                    existing_vehicle.relevance = RelevanceStatus.ARCHIVAL
                    existing_vehicle.is_manually_upserted = False
                    stats["archived"] += 1
                    logger.info("VIN %s archived after refresh", vin)
                else:
                    existing_vehicle.relevance = RelevanceStatus.ACTIVE
                    stats["reactivated_or_kept_active"] += 1
                    logger.info("VIN %s kept ACTIVE after refresh", vin)

                db.add(existing_vehicle)
                db.commit()
                stats["updated"] += 1

            except IntegrityError as e:
                db.rollback()
                logger.exception("VIN %s: IntegrityError: %s", vin, getattr(e, "orig", e))
                stats["errors"] += 1
            except Exception as e:
                db.rollback()
                logger.exception("VIN %s: update failed: %s", vin, e)
                stats["errors"] += 1

    logger.info("Expired auction update finished: %s", stats)
    return stats
