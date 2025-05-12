import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class BaseAppSettings(BaseModel):
    BASE_DIR: Path = Field(default_factory=lambda: Path(__file__).parent.parent)

    PATH_TO_DB: str = Field(
        default_factory=lambda: str(Path(__file__).parent.parent / "db" / "source" / "cars_and_beyond.db")
    )

    LOGIN_TIME_DAYS: int = 7


class Settings(BaseAppSettings):
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "test_user")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "test_password")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "test_host")
    POSTGRES_DB_PORT: int = int(os.getenv("POSTGRES_DB_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "test_db")

    SECRET_KEY_ACCESS: str = os.getenv("SECRET_KEY_ACCESS", os.urandom(32).hex())
    SECRET_KEY_REFRESH: str = os.getenv("SECRET_KEY_REFRESH", os.urandom(32).hex())
    SECRET_KEY_USER_INTERACTION: str = os.getenv("SECRET_KEY_USER_INTERACTION", os.urandom(32).hex())
    JWT_SIGNING_ALGORITHM: str = os.getenv("JWT_SIGNING_ALGORITHM", "HS256")

    SMTP_SERVER: str = os.getenv("SMTP_SERVER")
    SMTP_PORT: int = os.getenv("SMTP_PORT")
    SMTP_USER: str = os.getenv("SMTP_USER")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM")

    COOKIE_PATH: str = os.getenv("COOKIE_PATH")
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE")
    COOKIE_HTTPONLY: bool = os.getenv("COOKIE_HTTPONLY")
    COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE")

    PARSERS_AUTH_TOKEN: str = os.getenv("PARSERS_AUTH_TOKEN")

    S3_STORAGE_HOST: str = os.getenv("MINIO_HOST", "minio-theater")
    S3_STORAGE_PORT: int = os.getenv("MINIO_PORT", 9000)
    S3_STORAGE_ACCESS_KEY: str = os.getenv("MINIO_ROOT_USER", "minioadmin")
    S3_STORAGE_SECRET_KEY: str = os.getenv("MINIO_ROOT_PASSWORD", "some_password")
    S3_BUCKET_NAME: str = os.getenv("MINIO_STORAGE", "theater-storage")

    @property
    def S3_STORAGE_ENDPOINT(self) -> str:
        return f"http://{self.S3_STORAGE_HOST}:{self.S3_STORAGE_PORT}"


settings = Settings()
