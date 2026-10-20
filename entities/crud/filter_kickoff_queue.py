"""Synchronous queue claiming; application services own commit and rollback."""

from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from models.filter_kickoff_queue import (
    FILTER_QUEUE_DISPATCH_LOCK_KEY,
    FilterKickoffQueueModel,
    FilterKickoffQueueStatus,
)


def load_due_kickoffs(db: Session, now: datetime) -> list[FilterKickoffQueueModel]:
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": FILTER_QUEUE_DISPATCH_LOCK_KEY})
    return (
        db.execute(
            select(FilterKickoffQueueModel)
            .where(
                FilterKickoffQueueModel.status == FilterKickoffQueueStatus.SCHEDULED,
                FilterKickoffQueueModel.scheduled_at <= now,
            )
            .order_by(FilterKickoffQueueModel.scheduled_at.asc())
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
