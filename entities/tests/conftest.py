"""Test bootstrap: establish deterministic settings before application imports.

Application, database and worker fixtures live in support modules and are imported
only by the test directories that need them.
"""

import pytest

_test_environment = pytest.MonkeyPatch()
for name, value in {
    "ENVIRON": "test",
    "POSTGRES_USER": "test_user",
    "POSTGRES_PASSWORD": "test_password",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_DB_PORT": "1",
    "POSTGRES_DB": "test_db",
    "SECRET_KEY_ACCESS": "test_access_key",
    "SECRET_KEY_REFRESH": "test_refresh_key",
    "SECRET_KEY_USER_INTERACTION": "test_user_interaction_key",
    "JWT_SIGNING_ALGORITHM": "HS256",
    "ACCESS_KEY_TIMEDELTA_MINUTES": "5",
    "REFRESH_KEY_TIMEDELTA_MINUTES": "10",
    "USER_INTERACTION_KEY_TIMEDELTA_DAYS": "1",
    "MINIO_HOST": "127.0.0.1",
    "MINIO_PORT": "1",
    "MINIO_ROOT_USER": "test-storage-user",
    "MINIO_ROOT_PASSWORD": "test-storage-password",
    "MINIO_STORAGE": "test-bucket",
    "SMTP_SERVER": "127.0.0.1",
    "SMTP_PORT": "1",
    "SMTP_USER": "test-smtp-user",
    "SMTP_PASSWORD": "test-smtp-password",
    "EMAIL_FROM": "test@example.com",
    "COOKIE_PATH": "/",
    "COOKIE_SECURE": "false",
    "COOKIE_HTTPONLY": "true",
    "COOKIE_SAMESITE": "lax",
    "PARSERS_AUTH_TOKEN": "test-parsers-token",
    "APICAR_KEY": "test-apicar-key",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PORT": "1",
    "REDIS_DB": "0",
}.items():
    _test_environment.setenv(name, value)


def pytest_unconfigure(config):
    _test_environment.undo()


def pytest_addoption(parser):
    parser.addoption("--run-scraping-runtime", action="store_true", help="Run local browser/OCR smoke tests.")
    parser.addoption(
        "--run-postgres", action="store_true",
        help="Run PostgreSQL tests in a disposable local cluster (requires initdb and pg_ctl).",
    )
    parser.addoption(
        "--postgres-bin", default=None,
        help="Directory containing PostgreSQL initdb and pg_ctl executables.",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-scraping-runtime"):
        for item in items:
            if "scraping_runtime" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Pass --run-scraping-runtime for browser/OCR smoke tests"))
    if not config.getoption("--run-postgres"):
        skip = pytest.mark.skip(reason="Pass --run-postgres to start a disposable PostgreSQL cluster")
        for item in items:
            if "postgres" in item.keywords:
                item.add_marker(skip)
