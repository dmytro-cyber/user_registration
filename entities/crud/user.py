import datetime
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from models.user import UserModel, UserRoleModel
from typing import List, Optional, Tuple

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def create_user(
    db: AsyncSession,
    email: str,
    raw_password: str,
    first_name: str,
    last_name: str,
    phone_number: str,
    date_of_birth: datetime.date,
    role_id: int,
) -> UserModel:
    """
    Create a new user in the database.

    Args:
        db (AsyncSession): The database session.
        email (str): The user's email.
        raw_password (str): The raw password to hash.
        first_name (str): User's first name.
        last_name (str): User's last name.
        phone_number (str): User's phone number.
        date_of_birth (datetime.date): User's date of birth.
        role_id (int): The role ID to assign to the user.

    Returns:
        UserModel: The created user object.

    Raises:
        SQLAlchemyError: If there is an error during user creation.
    """
    try:
        new_user = UserModel.create(email=email, raw_password=raw_password)
        new_user.role_id = role_id
        new_user.first_name = first_name
        new_user.last_name = last_name
        new_user.phone_number = phone_number
        new_user.date_of_birth = date_of_birth
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        logger.info(f"User {new_user.email} successfully created with role_id: {new_user.role_id}")
        return new_user
    except SQLAlchemyError as e:
        logger.error(f"Error creating user with email {email}: {str(e)}")
        raise


async def get_user_by_email(db: AsyncSession, email: str) -> UserModel:
    """
    Retrieve a user by their email.

    Args:
        db (AsyncSession): The database session.
        email (str): The email of the user to find.

    Returns:
        UserModel: The user object if found, None otherwise.
    """
    result = await db.execute(select(UserModel).options(selectinload(UserModel.role)).where(UserModel.email == email))
    user = result.scalars().first()
    if user:
        logger.debug(f"Found user with email: {email}")
    else:
        logger.debug(f"No user found with email: {email}")
    return user


async def get_user_by_id(db: AsyncSession, user_id: int) -> UserModel:
    """
    Retrieve a user by their ID.

    Args:
        db (AsyncSession): The database session.
        user_id (int): The ID of the user to find.

    Returns:
        UserModel: The user object if found, None otherwise.
    """
    result = await db.execute(select(UserModel).options(selectinload(UserModel.role)).filter_by(id=user_id))
    user = result.scalar_one_or_none()
    if user:
        logger.debug(f"Found user with ID: {user_id}")
    else:
        logger.debug(f"No user found with ID: {user_id}")
    return user


async def get_all_roles(db: AsyncSession) -> List[UserRoleModel]:
    """
    Retrieve all user roles from the database.

    Args:
        db (AsyncSession): The database session.

    Returns:
        List[UserRoleModel]: List of all roles.
    """
    result = await db.execute(select(UserRoleModel))
    roles = result.scalars().all()
    logger.info(f"Fetched {len(roles)} roles from the database")
    return roles


async def get_role_by_name(db: AsyncSession, role_name: str) -> UserRoleModel:
    """
    Retrieve a role by its name.

    Args:
        db (AsyncSession): The database session.
        role_name (str): The name of the role to find.

    Returns:
        UserRoleModel: The role object if found, None otherwise.
    """
    result = await db.execute(select(UserRoleModel).where(UserRoleModel.name == role_name))
    role = result.scalars().first()
    if role:
        logger.debug(f"Found role with name: {role_name}")
    else:
        logger.debug(f"No role found with name: {role_name}")
    return role


async def update_user_role(db: AsyncSession, user: UserModel, role_id: int) -> None:
    """
    Update the role of a user.

    Args:
        db (AsyncSession): The database session.
        user (UserModel): The user to update.
        role_id (int): The new role ID.

    Raises:
        SQLAlchemyError: If there is an error during the update.
    """
    try:
        user.role_id = role_id
        db.add(user)
        await db.commit()
        logger.info(f"Updated role for user {user.email} to role_id: {role_id}")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error updating role for user {user.email}: {str(e)}")
        raise


async def update_user_password(db: AsyncSession, user: UserModel, new_password: str) -> None:
    """
    Update the password of a user.

    Args:
        db (AsyncSession): The database session.
        user (UserModel): The user to update.
        new_password (str): The new password.

    Raises:
        SQLAlchemyError: If there is an error during the update.
    """
    try:
        user.password = new_password
        await db.commit()
        logger.info(f"Password updated for user {user.email}")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error updating password for user {user.email}: {str(e)}")
        raise


async def update_user_info(db: AsyncSession, user: UserModel, updates: dict) -> UserModel:
    """
    Update user information with provided fields.

    Args:
        db (AsyncSession): The database session.
        user (UserModel): The user to update.
        updates (dict): Dictionary of fields to update.

    Returns:
        UserModel: The updated user object.

    Raises:
        SQLAlchemyError: If there is an error during the update.
    """
    try:
        for field, value in updates.items():
            if value is not None:
                setattr(user, field, value)
        await db.commit()
        await db.refresh(user)
        logger.info(f"Updated information for user {user.email}")
        return user
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error updating user {user.email} information: {str(e)}")
        raise


async def get_filtered_users(
    db: AsyncSession,
    page: int,
    page_size: int,
    role_id: Optional[int] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> Tuple[List[UserModel], int, int]:
    """
    Retrieve a paginated list of users with optional filtering.

    Args:
        db (AsyncSession): The database session.
        page (int): Page number.
        page_size (int): Number of users per page.
        role_id (Optional[int]): Filter by role ID.
        first_name (Optional[str]): Filter by first name.
        last_name (Optional[str]): Filter by last name.
        email (Optional[str]): Filter by email.
        phone (Optional[str]): Filter by phone number.

    Returns:
        Tuple[List[UserModel], int, int]: List of users, total count, and total pages.
    """
    query = select(UserModel).options(selectinload(UserModel.role))

    if role_id:
        query = query.filter(UserModel.role_id == role_id)
    if first_name:
        query = query.filter(UserModel.first_name.ilike(f"%{first_name}%"))
    if last_name:
        query = query.filter(UserModel.last_name.ilike(f"%{last_name}%"))
    if email:
        query = query.filter(UserModel.email.ilike(f"%{email}%"))
    if phone:
        query = query.filter(UserModel.phone_number.ilike(f"%{phone}%"))

    try:
        total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
        total_pages = (total_count + page_size - 1) // page_size

        result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
        users = result.scalars().all()
        logger.info(f"Fetched {len(users)} users (total: {total_count})")
        return users, total_count, total_pages
    except SQLAlchemyError as e:
        logger.error(f"Error fetching filtered users: {str(e)}")
        raise
