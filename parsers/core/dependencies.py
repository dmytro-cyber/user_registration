import os
import logging

from fastapi import HTTPException, status, Request

from core.config import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_settings() -> Settings:
    return Settings()


def get_token(request: Request) -> str:
    """
    Extracts the Bearer token from the Authorization header.

    :param request: FastAPI Request object.
    :return: Extracted token string.
    :raises HTTPException: If Authorization header is missing or invalid.
    """
    authorization: str = request.headers.get("X-Auth-Token")

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is missing",
        )

    if authorization != os.getenv("PARSERS_AUTH_TOKEN"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

