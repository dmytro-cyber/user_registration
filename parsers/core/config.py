import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class BaseAppSettings(BaseModel):
    BASE_DIR: Path = Field(default_factory=lambda: Path(__file__).parent.parent)

    LOGIN_TIME_DAYS: int = 7


class Settings(BaseAppSettings):

    SECRET_KEY_ACCESS: str = os.getenv("SECRET_KEY_ACCESS", os.urandom(32).hex())
    SECRET_KEY_REFRESH: str = os.getenv("SECRET_KEY_REFRESH", os.urandom(32).hex())
    SECRET_KEY_USER_INTERACTION: str = os.getenv("SECRET_KEY_USER_INTERACTION", os.urandom(32).hex())
    JWT_SIGNING_ALGORITHM: str = os.getenv("JWT_SIGNING_ALGORITHM", "HS256")

    SMTP_SERVER: str = os.getenv("SMTP_SERVER")
    SMTP_PORT: int = os.getenv("SMTP_PORT")
    SMTP_USER: str = os.getenv("SMTP_USER")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM")
    
    ENVIRON: str = os.getenv("ENVIRON", "prod")


settings = Settings()
