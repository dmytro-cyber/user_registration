import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from jose import ExpiredSignatureError, JWTError, jwt

from core.security.interfaces import JWTAuthManagerInterface
from exceptions import InvalidTokenError, TokenExpiredError

load_dotenv()


class JWTAuthManager(JWTAuthManagerInterface):
    """
    A manager for creating, decoding, and verifying JWT access and refresh tokens.
    """

    _ACCESS_KEY_TIMEDELTA_MINUTES = int(os.getenv("ACCESS_KEY_TIMEDELTA_MINUTES"))
    _REFRESH_KEY_TIMEDELTA_MINUTES = int(os.getenv("REFRESH_KEY_TIMEDELTA_MINUTES"))
    _USER_INTERACTION_KEY_TIMEDELTA_DAYS = int(os.getenv("USER_INTERACTION_KEY_TIMEDELTA_DAYS"))

    def __init__(
        self, secret_key_access: str, secret_key_refresh: str, secret_key_user_interaction: str, algorithm: str
    ):
        """
        Initialize the manager with secret keys and algorithm for token operations.
        """
        self._secret_key_access = secret_key_access
        self._secret_key_refresh = secret_key_refresh
        self._secret_key_user_interaction = secret_key_user_interaction
        self._algorithm = algorithm

    def _create_token(self, data: dict, secret_key: str, expires_delta: timedelta) -> str:
        """
        Create a JWT token with provided data, secret key, and expiration time.
        """
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + expires_delta
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, secret_key, algorithm=self._algorithm)

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a new access token with a default or specified expiration time.
        """
        return self._create_token(
            data,
            self._secret_key_access,
            expires_delta or timedelta(minutes=self._ACCESS_KEY_TIMEDELTA_MINUTES),
        )

    def create_refresh_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a new refresh token with a default or specified expiration time.
        """
        return self._create_token(
            data,
            self._secret_key_refresh,
            expires_delta or timedelta(minutes=self._REFRESH_KEY_TIMEDELTA_MINUTES),
        )

    def create_user_interaction_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a new refresh token with a default or specified expiration time.
        """
        return self._create_token(
            data,
            self._secret_key_user_interaction,
            expires_delta or timedelta(days=self._USER_INTERACTION_KEY_TIMEDELTA_DAYS),
        )

    def decode_access_token(self, token: str) -> dict:
        """
        Decode and validate an access token, returning the token's data.
        """
        try:
            return jwt.decode(token, self._secret_key_access, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError
        except JWTError:
            raise InvalidTokenError

    def decode_refresh_token(self, token: str) -> dict:
        """
        Decode and validate a refresh token, returning the token's data.
        """
        try:
            return jwt.decode(token, self._secret_key_refresh, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError
        except JWTError:
            raise InvalidTokenError

    def decode_user_interaction_token(self, code: str) -> dict:
        """
        Decode and validate a invitation code, returning the invitation's data.
        """
        try:
            return jwt.decode(code, self._secret_key_user_interaction, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError
        except JWTError:
            raise InvalidTokenError

    def verify_refresh_token_or_raise(self, token: str) -> None:
        """
        Verify a refresh token and raise an error if it's invalid or expired.
        """
        self.decode_refresh_token(token)

    def verify_access_token_or_raise(self, token: str) -> None:
        """
        Verify an access token and raise an error if it's invalid or expired.
        """
        self.decode_access_token(token)

    def verify_user_interaction_token_or_raise(self, code: str) -> None:
        """
        Verify an invitation code and raise an error if it's invalid or expired.
        """
        self.decode_user_interaction_token(code)
