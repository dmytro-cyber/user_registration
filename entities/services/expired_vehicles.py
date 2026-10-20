"""Transaction and failure policy for expired irrelevant vehicle cleanup."""

import logging
from datetime import datetime
from typing import TypedDict

from sqlalchemy.orm import Session

from crud.expired_vehicles import delete_expired_irrelevant_vehicles

logger = logging.getLogger(__name__)


class ExpiredCleanupResult(TypedDict):
    deleted_irrelevant: int
    errors: int


def cleanup_expired_irrelevant_vehicles(db: Session, now: datetime) -> ExpiredCleanupResult:
    result: ExpiredCleanupResult = {"deleted_irrelevant": 0, "errors": 0}
    try:
        result["deleted_irrelevant"] = delete_expired_irrelevant_vehicles(db, now)
        if result["deleted_irrelevant"]:
            db.commit()
        logger.info("Deleted IRRELEVANT expired cars: %s", result["deleted_irrelevant"])
    except Exception:
        db.rollback()
        logger.exception("Failed to delete irrelevant expired cars")
        result["errors"] += 1
    return result
