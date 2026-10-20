"""Fixtures required by this test group."""

from tests.support.celery import (
    isolate_celery,
)
from tests.support.database import (
    _test_db_file,
    anyio_backend,
    async_session_factory,
    create_car,
    db_session,
    db_session_sync,
    engine,
    engine_sync,
    gen_vin,
    reset_db,
    seed_roles,
    setup_roles,
    sqlite_fk_off,
    test_user,
    user_role,
    user_role_id,
)
from tests.support.tasks import (
    http_router_mock,
    mock_roi_and_fees,
    mock_s3_for_task,
    patch_task_sessionlocal,
    patch_task_settings,
)
