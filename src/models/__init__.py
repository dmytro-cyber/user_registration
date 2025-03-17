from sqlalchemy.orm import declarative_base

Base = declarative_base()

from .user import RefreshTokenModel as RefreshTokenModel
from .user import UserModel as UserModel