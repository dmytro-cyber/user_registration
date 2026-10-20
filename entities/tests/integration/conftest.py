"""Fixtures required by this test group."""

from tests.support.application import (
    as_admin,
    client,
    fresh_app_db,
    invite_code,
    jwt_manager,
    mock_verify_invite_broken,
    mock_verify_invite_ok,
    override_bidding_hub_user,
    override_current_user,
    override_db_dependency,
    override_settings_dependency,
    patch_admin_httpx_client,
    patch_admin_module_lock_busy,
    patch_filter_enqueue,
    patch_verify_invite,
    silence_app_loggers_for_tests,
    use_test_user,
)
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
