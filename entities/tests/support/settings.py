"""Harmless settings used in application and worker tests."""


class TestSettings:
    """
    Minimal test settings that mimic production interface with harmless defaults.
    """
    POSTGRES_USER = "test_user"
    POSTGRES_PASSWORD = "test_password"
    POSTGRES_HOST = "test_host"
    POSTGRES_DB_PORT = 5432
    POSTGRES_DB = "test_db"

    SECRET_KEY_ACCESS = "test_access_key"
    SECRET_KEY_REFRESH = "test_refresh_key"
    SECRET_KEY_USER_INTERACTION = "test_user_interaction_key"
    JWT_SIGNING_ALGORITHM = "HS256"

    MINIO_HOST = "minio-test"
    MINIO_PORT = 9000
    MINIO_ROOT_USER = "minioadmin"
    MINIO_ROOT_PASSWORD = "minioadmin"
    MINIO_STORAGE = "test-bucket"

    S3_STORAGE_HOST = "minio-test"
    S3_STORAGE_PORT = 9000
    S3_STORAGE_ACCESS_KEY = "minioadmin"
    S3_STORAGE_SECRET_KEY = "minioadmin"
    S3_BUCKET_NAME = "test-bucket"

    PARSERS_AUTH_TOKEN = "test-parsers-token"

    @property
    def S3_STORAGE_ENDPOINT(self) -> str:
        return f"http://{self.S3_STORAGE_HOST}:{self.S3_STORAGE_PORT}"
