import os
import logging

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.config import Settings
from core.security.interfaces import JWTAuthManagerInterface
from core.security.token_manager import JWTAuthManager
from sqlalchemy.orm import selectinload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_settings() -> Settings:
    # environment = os.getenv("ENVIRONMENT", "developing")
    # if environment == "testing":
    #     return TestingSettings()
    return Settings()


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


async def get_current_user(request: Request, settings: Settings = Depends(get_settings)):
    from db.session import get_db
    from models.user import UserModel, UserRoleModel

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
