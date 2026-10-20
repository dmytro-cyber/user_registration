# app/services/filter_kickoff_queue.py

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from crud.filter_kickoff_queue import load_due_kickoffs
from domain.filter_kickoff import FilterDispatchResult, FilterKickoffPublisher
from models.filter_kickoff_queue import (
    FILTER_KICKOFF_INTERVAL,
    FILTER_QUEUE_ENQUEUE_LOCK_KEY,
    FilterKickoffQueueModel,
    FilterKickoffQueueStatus,
)


async def enqueue_filter_kickoff(
    db: AsyncSession,
    filter_id: int,
) -> FilterKickoffQueueModel:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": FILTER_QUEUE_ENQUEUE_LOCK_KEY},
    )

    now = datetime.now(timezone.utc)

    stmt = (
        select(FilterKickoffQueueModel)
        .where(
            FilterKickoffQueueModel.status.in_(
                [
                    FilterKickoffQueueStatus.SCHEDULED,
                    FilterKickoffQueueStatus.DISPATCHED,
                ]
            )
        )
        .order_by(FilterKickoffQueueModel.scheduled_at.desc())
        .limit(1)
    )

    last_job = (await db.execute(stmt)).scalar_one_or_none()

    if last_job is None:
        scheduled_at = now
    else:
        scheduled_at = max(now, last_job.scheduled_at + FILTER_KICKOFF_INTERVAL)

    queue_item = FilterKickoffQueueModel(
        filter_id=filter_id,
        status=FilterKickoffQueueStatus.SCHEDULED,
        scheduled_at=scheduled_at,
    )
    db.add(queue_item)
    await db.flush()

    return queue_item


def dispatch_due_filter_kickoffs(
    db: Session,
    publisher: FilterKickoffPublisher,
    now: datetime,
) -> FilterDispatchResult:
    dispatched = 0
    failed = 0
    try:
        jobs = load_due_kickoffs(db, now)
        for job in jobs:
            try:
                publication = publisher.publish(job.filter_id, job.id)
                job.status = FilterKickoffQueueStatus.DISPATCHED
                job.dispatched_at = now
                job.celery_task_id = publication.id
                job.attempts += 1
                job.last_error = None
                dispatched += 1
            except Exception as exc:
                job.status = FilterKickoffQueueStatus.FAILED
                job.finished_at = now
                job.last_error = str(exc)
                failed += 1
        db.commit()
        return {"status": "ok", "dispatched": dispatched, "failed": failed}
    except Exception:
        db.rollback()
        raise
