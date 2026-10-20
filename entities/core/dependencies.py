import logging
import os

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from core.config import Settings
from core.security.interfaces import JWTAuthManagerInterface
from core.security.token_manager import JWTAuthManager
from db.session import get_db
from domain.iaai_fees import IaaiFeeSource
from domain.vehicle_ingestion import VehicleEnrichmentPublisher
from domain.vehicle_source import VehicleDataSource
from models.user import UserModel
from storages import S3StorageClient, S3StorageInterface

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_settings() -> Settings:
    return Settings()


def get_iaai_fee_source() -> IaaiFeeSource:
    from integrations.iaai_fees import ParsingIaaiFeeSource
    from integrations.parsing import ParsingClient

    return ParsingIaaiFeeSource(ParsingClient)


def get_vehicle_data_source() -> VehicleDataSource:
    from integrations.apicar.vehicle_source import ApicarVehicleDataSource

    return ApicarVehicleDataSource()


def get_vehicle_enrichment_publisher() -> VehicleEnrichmentPublisher:
    from core.celery_config import app as celery_app
    from integrations.vehicle_tasks import CeleryVehicleEnrichmentPublisher

    return CeleryVehicleEnrichmentPublisher(celery_app)


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



async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> UserModel:
    """
    Retrieve current authenticated user from JWT cookie.
    """

    token: str | None = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
        )

    jwt_auth_manager = get_jwt_auth_manager(settings)

    try:
        payload = jwt_auth_manager.decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id: int | None = payload.get("user_id")

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await db.execute(
        select(UserModel)
        .options(selectinload(UserModel.role))
        .where(UserModel.id == user_id)
    )

    user: UserModel | None = result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user
