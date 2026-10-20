import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text

from db.config import DatabaseConfig
from db.factories import create_sync_database
from models.admin import FilterModel
from models.filter_kickoff_queue import (
    FILTER_QUEUE_DISPATCH_LOCK_KEY,
    FilterKickoffQueueModel,
    FilterKickoffQueueStatus,
)
from services.filter_kickoff_queue import dispatch_due_filter_kickoffs

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def sync_database(postgres_sessions):
    database = create_sync_database(DatabaseConfig(url=postgres_sessions.kw["bind"].url))
    try:
        yield database
    finally:
        database.engine.dispose()


async def seed_jobs(sessions, specifications):
    ids = []
    async with sessions() as db:
        for scheduled_at, status in specifications:
            filt = FilterModel(make="Toyota")
            db.add(filt)
            await db.flush()
            job = FilterKickoffQueueModel(
                filter_id=filt.id, status=status, scheduled_at=scheduled_at, attempts=2, last_error="old error"
            )
            db.add(job)
            await db.flush()
            ids.append((filt.id, job.id))
        await db.commit()
    return ids


class Publisher:
    def __init__(self, failed_ids=()):
        self.failed_ids = failed_ids
        self.calls = []

    def publish(self, filter_id, job_id):
        self.calls.append((filter_id, job_id))
        if job_id in self.failed_ids:
            raise RuntimeError("broker unavailable")
        return SimpleNamespace(id=f"task-{job_id}")


async def test_due_order_success_failure_and_unmodified_future_jobs(sync_database, postgres_sessions):
    scheduled = FilterKickoffQueueStatus.SCHEDULED
    ids = await seed_jobs(
        postgres_sessions,
        [
            (NOW - timedelta(seconds=2), scheduled),
            (NOW - timedelta(seconds=1), scheduled),
            (NOW + timedelta(seconds=1), scheduled),
            (NOW, FilterKickoffQueueStatus.DONE),
        ],
    )
    publisher = Publisher(failed_ids={ids[1][1]})
    with sync_database.sessions() as db:
        assert dispatch_due_filter_kickoffs(db, publisher, NOW) == {"status": "ok", "dispatched": 1, "failed": 1}
    assert publisher.calls == ids[:2]
    async with postgres_sessions() as db:
        jobs = {row.id: row for row in (await db.execute(select(FilterKickoffQueueModel))).scalars()}
        success, failed, future = [jobs[item[1]] for item in ids[:3]]
        assert success.status == FilterKickoffQueueStatus.DISPATCHED
        assert success.dispatched_at == NOW and success.celery_task_id == f"task-{success.id}"
        assert success.attempts == 3 and success.last_error is None
        assert failed.status == FilterKickoffQueueStatus.FAILED
        assert failed.finished_at == NOW and failed.attempts == 2 and failed.last_error == "broker unavailable"
        assert future.status == scheduled and future.attempts == 2 and future.last_error == "old error"
    with sync_database.sessions() as db:
        assert dispatch_due_filter_kickoffs(db, Publisher(), NOW) == {"status": "ok", "dispatched": 0, "failed": 0}


async def test_publication_still_precedes_commit_and_commit_failure_leaves_job_scheduled(
    sync_database, postgres_sessions, monkeypatch
):
    ids = await seed_jobs(postgres_sessions, [(NOW, FilterKickoffQueueStatus.SCHEDULED)])
    publisher = Publisher()
    original_publish = publisher.publish
    observed = []

    def publish(filter_id, job_id):
        with sync_database.sessions() as observer:
            observed.append(observer.get(FilterKickoffQueueModel, job_id).status)
        return original_publish(filter_id, job_id)

    publisher.publish = publish
    with sync_database.sessions() as db:
        monkeypatch.setattr(db, "commit", Mock(side_effect=RuntimeError("commit failed")))
        with pytest.raises(RuntimeError, match="commit failed"):
            dispatch_due_filter_kickoffs(db, publisher, NOW)
    assert publisher.calls == ids
    assert observed == [FilterKickoffQueueStatus.SCHEDULED]
    async with postgres_sessions() as db:
        job = (await db.execute(select(FilterKickoffQueueModel))).scalar_one()
        assert job.status == FilterKickoffQueueStatus.SCHEDULED and job.attempts == 2
        assert job.celery_task_id is None and job.last_error == "old error"
        assert await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": FILTER_QUEUE_DISPATCH_LOCK_KEY})


async def test_locked_queue_row_is_skipped_then_dispatched_after_release(sync_database, postgres_sessions):
    ids = await seed_jobs(postgres_sessions, [(NOW, FilterKickoffQueueStatus.SCHEDULED)])
    publisher = Publisher()
    async with postgres_sessions() as holder:
        await holder.execute(select(FilterKickoffQueueModel).with_for_update())
        with sync_database.sessions() as db:
            assert dispatch_due_filter_kickoffs(db, publisher, NOW)["dispatched"] == 0
        assert publisher.calls == []
        await holder.rollback()
    with sync_database.sessions() as db:
        assert dispatch_due_filter_kickoffs(db, publisher, NOW)["dispatched"] == 1
    assert publisher.calls == ids


async def test_concurrent_dispatchers_wait_for_advisory_lock_and_do_not_republish(sync_database, postgres_sessions):
    ids = await seed_jobs(postgres_sessions, [(NOW, FilterKickoffQueueStatus.SCHEDULED)])
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    second_pid = []
    first_publisher, second_publisher = Publisher(), Publisher()
    publish = first_publisher.publish

    def hold_publish(filter_id, job_id):
        entered.set()
        assert release.wait(10), "Timed out waiting to release dispatcher"
        return publish(filter_id, job_id)

    first_publisher.publish = hold_publish

    def run(publisher, identify=False):
        with sync_database.sessions() as db:
            if identify:
                second_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
                second_started.set()
            return dispatch_due_filter_kickoffs(db, publisher, NOW)

    tasks = [asyncio.create_task(asyncio.to_thread(run, first_publisher))]
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        tasks.append(asyncio.create_task(asyncio.to_thread(run, second_publisher, True)))
        assert await asyncio.to_thread(second_started.wait, 5)
        deadline = asyncio.get_running_loop().time() + 5
        async with postgres_sessions() as observer:
            while not await observer.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": second_pid[0]}
            ):
                assert asyncio.get_running_loop().time() < deadline, "Second dispatcher did not wait on advisory lock"
                await asyncio.sleep(0.01)
        assert not tasks[1].done()
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert results == [{"status": "ok", "dispatched": 1, "failed": 0}, {"status": "ok", "dispatched": 0, "failed": 0}]
    assert first_publisher.calls == ids and second_publisher.calls == []


async def test_celery_dispatcher_keeps_task_name_kwargs_and_result(sync_database, postgres_sessions, monkeypatch):
    from tasks import task

    ids = await seed_jobs(postgres_sessions, [(NOW, FilterKickoffQueueStatus.SCHEDULED)])
    monkeypatch.setattr(task, "SessionLocal", sync_database.sessions)
    send = Mock(return_value=SimpleNamespace(id="published-task"))
    monkeypatch.setattr(task.app, "send_task", send)
    assert task.dispatch_due_filter_kickoff_jobs.name == "tasks.task.dispatch_due_filter_kickoff_jobs"
    assert task.dispatch_due_filter_kickoff_jobs() == {"status": "ok", "dispatched": 1, "failed": 0}
    send.assert_called_once_with(
        "tasks.task.kickoff_parse_for_filter",
        kwargs={"filter_id": ids[0][0], "lock_token": str(ids[0][1])},
        queue="car_parsing_queue",
    )
