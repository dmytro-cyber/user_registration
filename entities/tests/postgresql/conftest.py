"""Disposable PostgreSQL profile. Never accepts a URL for an existing database."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest


def _postgres_bin(config):
    configured = config.getoption("--postgres-bin")
    candidates = [Path(configured)] if configured else [
        Path(shutil.which("initdb")).parent if shutil.which("initdb") else Path("/nonexistent"),
        Path("/opt/homebrew/opt/postgresql@16/bin"),
        Path("/usr/lib/postgresql/16/bin"),
    ]
    for candidate in candidates:
        candidate = (candidate / "initdb").resolve().parent
        if all((candidate / name).is_file() for name in ("initdb", "pg_ctl", "postgres")):
            return candidate
    pytest.fail("PostgreSQL binaries unavailable; supply --postgres-bin=/path/to/bin")


def _run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if result.returncode:
        pytest.fail(f"PostgreSQL command failed: {args[0]}\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session")
def postgres_cluster(pytestconfig):
    from sqlalchemy import URL

    if not pytestconfig.getoption("--run-postgres"):
        pytest.skip("Pass --run-postgres to start a disposable PostgreSQL cluster")
    binaries = _postgres_bin(pytestconfig)
    # A short path is required by Unix-domain sockets (typically limited to 108 bytes).
    with tempfile.TemporaryDirectory(prefix="ur-pg-") as directory:
        root = Path(directory)
        data = root / "data"
        socket = root / "socket"
        socket.mkdir()
        _run(
            str(binaries / "initdb"), "-D", str(data), "--username=test_user",
            "--auth=trust", "--no-locale", "--encoding=UTF8",
        )
        try:
            # No TCP listener. Only this temporary cluster's private socket is used.
            _run(
                str(binaries / "pg_ctl"), "-D", str(data), "-l", str(root / "server.log"),
                "-o", f"-h '' -k '{socket}' -p 55439 -F -c unix_socket_permissions=0700",
                "-w", "start",
            )
            yield URL.create(
                "postgresql+asyncpg", username="test_user", host=str(socket),
                port=55439, database="postgres",
            )
        finally:
            if (data / "postmaster.pid").exists():
                _run(str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop")


@pytest.fixture
async def postgres_sessions(postgres_cluster):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from models import Base

    # The generated name belongs exclusively to this test's temporary cluster.
    database = f"test_{uuid4().hex}"
    admin = create_async_engine(postgres_cluster, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    engine = create_async_engine(postgres_cluster.set(database=database))
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()
            async with admin.connect() as connection:
                await connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
    finally:
        await admin.dispose()
