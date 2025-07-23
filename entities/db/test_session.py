from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# SQLite URL for async testing (in-memory for isolation)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Create async engine for SQLite
engine = create_async_engine(TEST_DATABASE_URL, echo=True)

# Create async session factory
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_test_db_session():
    """Async context manager for test database session."""
    from models import Base

    async with engine.begin() as conn:
        # Create tables in memory
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
