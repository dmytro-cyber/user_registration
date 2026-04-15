from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.filter_kickoff_queue import (
    FilterKickoffQueueModel,
    FilterKickoffQueueStatus,
    FILTER_KICKOFF_INTERVAL,
    FILTER_QUEUE_ENQUEUE_LOCK_KEY
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

    job = FilterKickoffQueueModel(
        filter_id=filter_id,
        status=FilterKickoffQueueStatus.SCHEDULED,
        scheduled_at=scheduled_at,
    )
    db.add(job)
    await db.flush()

    return job