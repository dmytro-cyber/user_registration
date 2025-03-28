from fastapi import Response
from core.config import settings


def set_token_cookie(response: Response, key: str, value: str, max_age: int):
    response.set_cookie(
        key=key,
        value=value,
        path=settings.COOKIE_PATH,
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=max_age
    )


def delete_token_cookie(response: Response, key: str):
    response.delete_cookie(
        key=key,
        path=settings.COOKIE_PATH,
        samesite=settings.COOKIE_SAMESITE
    )
