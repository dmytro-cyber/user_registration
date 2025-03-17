import os

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.config import Settings
from core.security.http import get_token
from core.security.interfaces import JWTAuthManagerInterface
from core.security.token_manager import JWTAuthManager


def get_settings() -> Settings:
    # environment = os.getenv("ENVIRONMENT", "developing")
    # if environment == "testing":
    #     return TestingSettings()
    return Settings()


def get_jwt_auth_manager(
    settings: Settings = Depends(get_settings),
) -> JWTAuthManagerInterface:
    return JWTAuthManager(
        secret_key_access=settings.SECRET_KEY_ACCESS,
        secret_key_refresh=settings.SECRET_KEY_REFRESH,
        algorithm=settings.JWT_SIGNING_ALGORITHM,
    )


async def get_current_user(request: Request, settings: Settings = Depends(get_settings)):
    from db import get_db
    from models.user import UserModel
    
    token = request.cookies.get("access_token")
    
    db: AsyncSession = await anext(get_db())
    try:
        payload = JWTAuthManager(
            secret_key_access=settings.SECRET_KEY_ACCESS,
            secret_key_refresh=settings.SECRET_KEY_REFRESH,
            algorithm=settings.JWT_SIGNING_ALGORITHM,
        ).decode_access_token(token)

        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

        result = await db.execute(select(UserModel).filter(UserModel.id == user_id))
        user = result.scalars().first()

        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

        return user

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Could not validate token: {str(e)}")
