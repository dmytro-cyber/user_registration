from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import ExpiredSignatureError, JWTError, jwt
from exceptions import InvalidTokenError, TokenExpiredError
from core.security.interfaces import JWTAuthManagerInterface
from typing import Dict
import random, string


class MockJWTAuthManager(JWTAuthManagerInterface):
    """
    A mock manager for creating, decoding, and verifying access and update tokens.
    It is used for tests.
    """

    _ACCESS_KEY_TIMEDELTA_MINUTES = 15  # Тривалість часу для access token (в хвилинах)
    _REFRESH_KEY_TIMEDELTA_MINUTES = 30  # Тривалість часу для refresh token (в хвилинах)

    def __init__(self):
        """
        Initializing the manager with secret keys and an algorithm for token operations.
        """
        self._secret_key_access = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        self._secret_key_refresh = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        self._algorithm = "HS256"

    def _create_token(self, data: dict, secret_key: str, expires_delta: timedelta) -> str:
        """
        Creating a JWT token with the provided data, secret key, and expiration time.
        """
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + expires_delta
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, secret_key, algorithm=self._algorithm)

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a new access token with a standard or specified expiration time.
        """
        return self._create_token(
            data,
            self._secret_key_access,
            expires_delta or timedelta(minutes=self._ACCESS_KEY_TIMEDELTA_MINUTES),
        )

    def create_refresh_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a new update token with a standard or specified expiration time.
        """
        return self._create_token(
            data,
            self._secret_key_refresh,
            expires_delta or timedelta(minutes=self._REFRESH_KEY_TIMEDELTA_MINUTES),
        )

    def create_invitation_code(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create an invitation code with a standard or specified expiration time.
        """
        return self._create_token(
            data,
            self._secret_key_refresh,
            expires_delta or timedelta(days=7),
        )

    def decode_access_token(self, token: str) -> dict:
        """
        Decode and verify the access token, returning the token data.
        """
        try:
            return jwt.decode(token, self._secret_key_access, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError
        except JWTError:
            raise InvalidTokenError

    def decode_refresh_token(self, token: str) -> dict:
        """
        Decode and verify the update token, returning the token data.
        """
        try:
            return jwt.decode(token, self._secret_key_refresh, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError
        except JWTError:
            raise InvalidTokenError

    def decode_invite_code(self, code: str) -> dict:
        """
        Decode and verify the invitation code, returning the invitation data.
        """
        try:
            return jwt.decode(code, self._secret_key_refresh, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError
        except JWTError:
            raise InvalidTokenError

    def verify_refresh_token_or_raise(self, token: str) -> None:
        """
        Checking the renewal token and raising an error if the token is invalid or expired.
        """
        self.decode_refresh_token(token)

    def verify_access_token_or_raise(self, token: str) -> None:
        """
        Verifies the access token and raises an error if the token is invalid or expired.
        """
        self.decode_access_token(token)

    def create_expired_token(self, data: dict, expires_delta: Optional[timedelta] = None):
        """
        Create expired token.
        """
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) - timedelta(days=7)
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, self._secret_key_refresh, algorithm=self._algorithm)
