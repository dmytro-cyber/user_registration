# Entities tests

Run from `entities/` with the Poetry environment for this service. Python 3.12 is the verified interpreter for the current lockfile.

```sh
poetry install --no-root --with scraping
poetry run pytest
```

The default run uses SQLite and skips PostgreSQL and browser-runtime profiles. Test settings, including JWT TTLs, are supplied by the root conftest before application imports. No local `.env` is needed. The environment overrides are restored at pytest shutdown. The optional scraping group supplies dependencies for transferred parser tests; browser-specific unit modules skip when their optional dependencies are absent.

## Test boundaries

| Path | Dependencies |
| --- | --- |
| `unit/domain/` | Pure calculations; no FastAPI app, settings, SQLAlchemy, Celery or HTTP client imports |
| `unit/crud/` | SQLite fixtures and broker publication stub |
| `unit/tasks/` | SQLite and worker-specific fixtures; no FastAPI app |
| `unit/integrations/` | External clients, conversion, transferred scraper logic and an embedded in-memory Celery worker |
| `integration/api/` | FastAPI app, dependency overrides, SQLite and explicit integration stubs |
| `postgresql/` | Real PostgreSQL transactions and concurrent sessions in a disposable cluster |
| `runtime/` | Explicit browser/OCR smoke profile with synthetic local data |

Shared fixtures are defined in `support/` and explicitly imported by each group's conftest. SQLite fixtures allocate a new database file for every test, so commits cannot leak records into another test. Tests under `unit/crud/` and `unit/tasks/` exercise persistence and are not pure unit tests despite their legacy directory names.

```sh
poetry run pytest tests/unit/domain
poetry run pytest tests/integration/api/bidding_hub_test.py
```

Neither `PYTHONPATH`, `--confcutdir` nor externally supplied JWT TTL variables are needed.

## PostgreSQL profile

Requires local `initdb`, `pg_ctl` and `postgres` executables from the same PostgreSQL installation. The profile discovers server binaries on PATH, Homebrew PostgreSQL 16 or `/usr/lib/postgresql/16/bin`. Override discovery with `--postgres-bin`.

```sh
poetry run pytest tests/postgresql --run-postgres
poetry run pytest --run-postgres
poetry run pytest tests/postgresql --run-postgres --postgres-bin=/path/to/postgresql/bin
```

The fixture creates a temporary cluster with no TCP listener, connects through its private Unix socket and creates a separate database for each test. It does not accept an external database URL. Test databases are removed and the cluster is stopped and deleted during teardown. The tests use ORM metadata to create their schema; they do not validate Alembic upgrade history.

Current PostgreSQL coverage:

- Concurrent actual-bid updates serialize on the vehicle row; each history entry records the preceding committed bid.
- A foreign-key failure in history insertion rolls back vehicle financials and releases the row lock.
- Concurrent filter enqueues serialize through the advisory transaction lock and reserve slots 90 minutes apart.
- Bulk imports preserve VIN deduplication, manual edits, parse lifecycle decisions and exact enrichment payloads.
- A failed child write rolls back parent and child data together; queue publication starts after commit. Broker failures retain the existing 500 response and leave committed data and earlier publications intact.

Concurrency tests observe `pg_blocking_pids` before releasing the first transaction, rather than relying on a fixed delay to assume the second transaction is waiting.

SQLite HTTP tests stub `enqueue_filter_kickoff` at the integration boundary. Its PostgreSQL SQL and locking are covered by the real PostgreSQL profile, rather than emulated in SQLite.

## Browser/OCR profile

```sh
poetry run playwright install chromium
poetry run pytest tests/runtime --run-scraping-runtime
```

The Playwright check renders local HTML. Selenium additionally needs `CHROME_BINARY` and `CHROMEDRIVER_PATH`; OCR needs Tesseract and Cairo. Missing system binaries are reported as skips. The scraping Docker target provides all three runtimes, and CI executes the profile inside that image. These tests do not authenticate to DealerCenter, fetch real auction data or send email.

## Contracts and task execution

`fixtures/contracts/parsers-openapi.json` records the original parsers API before consolidation. Tests compare every transferred operation and schema against it. APICAR calls use mocked external transports; browser/OCR HTTP tests stub the queue boundary. A separate embedded Celery worker test verifies dispatch, result delivery, worker-thread isolation and event-loop reuse with an in-memory broker/backend.

The PostgreSQL profile also verifies the periodic ingestion bridge against the real bulk application service. The migration does not replace database behavior with an HTTP mock.
