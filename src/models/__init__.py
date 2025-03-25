from sqlalchemy.orm import declarative_base

Base = declarative_base()

from .user import UserModel as UserModel
from .user import UserRoleModel as UserRoleModel
from .user import UserRoleEnum as UserRoleEnum
from .vehicle import Photo as Photo
from .vehicle import Car as Car
from .vehicle import Part as Part
