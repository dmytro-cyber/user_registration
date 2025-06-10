import os
import logging

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.config import Settings
from core.security.interfaces import JWTAuthManagerInterface
from core.security.token_manager import JWTAuthManager
from sqlalchemy.orm import selectinload
from storages import S3StorageInterface, S3StorageClient

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


def get_jwt_auth_manager(
    settings: Settings = Depends(get_settings),
) -> JWTAuthManagerInterface:
    logger.info("Initializing JWTAuthManager with settings")
    logger.debug(f"Settings: {settings}")
    return JWTAuthManager(
        secret_key_access=settings.SECRET_KEY_ACCESS,
        secret_key_refresh=settings.SECRET_KEY_REFRESH,
        secret_key_user_interaction=settings.SECRET_KEY_USER_INTERACTION,
        algorithm=settings.JWT_SIGNING_ALGORITHM,
    )


def get_s3_storage_client() -> S3StorageInterface:
    settings = get_settings()
    return S3StorageClient(
        endpoint_url=settings.S3_STORAGE_ENDPOINT,
        access_key=settings.S3_STORAGE_ACCESS_KEY,
        secret_key=settings.S3_STORAGE_SECRET_KEY,
        bucket_name=settings.S3_BUCKET_NAME,
    )


async def get_current_user(request: Request, settings: Settings = Depends(get_settings)):
    from db.session import get_db
    from models.user import UserModel

    token = request.cookies.get("access_token")

    db: AsyncSession = await anext(get_db())
    jwt_auth_manager = get_jwt_auth_manager(settings)
    try:
        payload = jwt_auth_manager.decode_access_token(token)

        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

        result = await db.execute(
            select(UserModel).options(selectinload(UserModel.role)).filter(UserModel.id == user_id)
        )
        user = result.scalars().first()

        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

        return user

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Could not validate token: {str(e)}")
