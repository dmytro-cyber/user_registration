"""Database connection configuration shared by API, jobs and migration tooling."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import URL

if TYPE_CHECKING:
    from core.config import Settings


@dataclass(frozen=True)
class DatabaseConfig:
    url: URL
    pool_size: int = 50
    max_overflow: int = 10
    pool_timeout: float = 30
    pool_recycle: int = 1800
    echo: bool = False

    @classmethod
    def from_settings(cls, settings: "Settings") -> "DatabaseConfig":
        return cls(
            url=URL.create(
                "postgresql+asyncpg",
                username=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_DB_PORT,
                database=settings.POSTGRES_DB,
            ),
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            pool_recycle=settings.DB_POOL_RECYCLE,
            echo=settings.DB_ECHO_SQL,
        )

    def engine_options(self) -> dict:
        return {
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_timeout": self.pool_timeout,
            "pool_recycle": self.pool_recycle,
            "pool_pre_ping": True,
            "echo": self.echo,
            "hide_parameters": True,
        }
