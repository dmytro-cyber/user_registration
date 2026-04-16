# app/models/filter_kickoff_queue.py

import enum
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from models import Base


FILTER_KICKOFF_INTERVAL = timedelta(minutes=90)

FILTER_QUEUE_ENQUEUE_LOCK_KEY = 910001
FILTER_QUEUE_DISPATCH_LOCK_KEY = 910002


class FilterKickoffQueueStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    DISPATCHED = "dispatched"
    DONE = "done"
    FAILED = "failed"


class FilterKickoffQueueModel(Base):
    __tablename__ = "filter_kickoff_queue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    filter_id: Mapped[int] = mapped_column(
        ForeignKey("filters.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    status: Mapped[FilterKickoffQueueStatus] = mapped_column(
        Enum(FilterKickoffQueueStatus, name="filter_kickoff_queue_status"),
        nullable=False,
        default=FilterKickoffQueueStatus.SCHEDULED,
        index=True,
    )

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )