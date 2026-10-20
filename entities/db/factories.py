"""Explicit engine creation; importing this module creates no database resources."""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from db.config import DatabaseConfig


@dataclass(frozen=True)
class AsyncDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class SyncDatabase:
    engine: Engine
    sessions: sessionmaker[Session]


def create_async_database(config: DatabaseConfig) -> AsyncDatabase:
    engine = create_async_engine(config.url, **config.engine_options())
    return AsyncDatabase(engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))


def create_sync_database(config: DatabaseConfig) -> SyncDatabase:
    engine = create_engine(config.url.set(drivername="postgresql+psycopg2"), **config.engine_options())
    return SyncDatabase(engine, sessionmaker(engine, class_=Session, autoflush=False, expire_on_commit=True))
