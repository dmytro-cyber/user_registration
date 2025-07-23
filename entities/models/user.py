import enum
from datetime import date, datetime, timedelta, timezone
from typing import List

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from core.security.passwords import hash_password, verify_password
from core.security.utils import generate_secure_token
from models import Base
from models.validators import user as validators


class UserRoleEnum(str, enum.Enum):
    USER = "user"
    VEHICLE_MANAGER = "vehicle_manager"
    PART_MANAGER = "part_manager"
    ADMIN = "admin"


class UserRoleModel(Base):
    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[UserRoleEnum] = mapped_column(Enum(UserRoleEnum), nullable=False, unique=True)

    users: Mapped[List["UserModel"]] = relationship("UserModel", back_populates="role")

    def __repr__(self):
        return f"<UserRoleModel(id={self.id}, name={self.name})>"


user_likes = Table(
    "user_likes",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("car_id", Integer, ForeignKey("cars.id"), primary_key=True),
)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String, nullable=True)
    last_name: Mapped[str] = mapped_column(String, nullable=True)
    phone_number: Mapped[str] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    temp_email: Mapped[str] = mapped_column(String, nullable=True)
    _hashed_password: Mapped[str] = mapped_column(String, nullable=False)

    history = relationship("HistoryModel", back_populates="user", cascade="all, delete-orphan")
    role_id: Mapped[int] = mapped_column(ForeignKey("user_roles.id", ondelete="CASCADE"), nullable=False)
    role: Mapped["UserRoleModel"] = relationship("UserRoleModel", back_populates="users")
    liked_cars = relationship("CarModel", secondary=user_likes, back_populates="liked_by")

    def __repr__(self):
        return f"<UserModel(id={self.id}, email={self.email})>"

    def has_role(self, role_name: UserRoleEnum) -> bool:
        return self.role.name == role_name

    def role_in(self, role_names=List[UserRoleEnum]) -> bool:
        return self.role.name in role_names

    @classmethod
    def create(cls, email: str, raw_password: str) -> "UserModel":
        """
        Factory method to create a new UserModel instance.
        """
        user = cls(email=email)
        user.password = raw_password
        return user

    @property
    def password(self) -> None:
        raise AttributeError("Password is write-only. Use the setter to set the password.")

    @password.setter
    def password(self, raw_password: str) -> None:
        """
        Set the user's password after validating its strength and hashing it.
        """
        validators.validate_password_strength(raw_password)
        self._hashed_password = hash_password(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        """
        Verify the provided password against the stored hashed password.
        """
        return verify_password(raw_password, self._hashed_password)

    @validates("email")
    def validate_email(self, key, value):
        return validators.validate_email(value.lower())


user_likes = Table(
    "user_likes",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("car_id", Integer, ForeignKey("cars.id"), primary_key=True),
    Index("ix_user_car", "user_id", "car_id"),
    extend_existing=True,
)
