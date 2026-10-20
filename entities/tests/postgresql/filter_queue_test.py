import asyncio

import pytest
from sqlalchemy import func, select, text

from models.admin import FilterModel
from models.filter_kickoff_queue import FILTER_KICKOFF_INTERVAL, FilterKickoffQueueModel, FilterKickoffQueueStatus
from services.filter_kickoff_queue import enqueue_filter_kickoff

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_concurrent_enqueues_reserve_distinct_scheduled_slots(postgres_sessions):
    async with postgres_sessions() as seed:
        filters = [FilterModel(make="Toyota"), FilterModel(make="Ford")]
        seed.add_all(filters)
        await seed.commit()
        first_id, second_id = (item.id for item in filters)

    async with postgres_sessions() as first, postgres_sessions() as second, postgres_sessions() as observer:
        first_job = await enqueue_filter_kickoff(first, first_id)
        second_pid = (await second.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        waiting = asyncio.create_task(enqueue_filter_kickoff(second, second_id))
        try:
            async with asyncio.timeout(5):
                while not (await observer.execute(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": second_pid},
                )).scalar_one():
                    await asyncio.sleep(0.01)
            # Queueing does not commit its caller's transaction.
            assert (await observer.execute(select(func.count()).select_from(FilterKickoffQueueModel))).scalar_one() == 0
            await first.commit()
            second_job = await asyncio.wait_for(waiting, timeout=5)
            await second.commit()
            assert second_job.scheduled_at - first_job.scheduled_at == FILTER_KICKOFF_INTERVAL
        finally:
            if not waiting.done():
                waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    async with postgres_sessions() as db:
        jobs = (await db.execute(
            select(FilterKickoffQueueModel).order_by(FilterKickoffQueueModel.scheduled_at)
        )).scalars().all()
        assert [job.filter_id for job in jobs] == [first_id, second_id]
        assert all(job.status == FilterKickoffQueueStatus.SCHEDULED for job in jobs)
