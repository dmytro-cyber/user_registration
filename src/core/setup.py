from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.user import UserRoleEnum, UserRoleModel
from db.session import SessionLocal        
from sqlalchemy.orm import selectinload
from passlib.context import CryptContext
from models import UserModel, UserRoleModel, UserRoleEnum
from sqlalchemy.exc import IntegrityError

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def create_roles():
    async with SessionLocal() as session:
        result_roles = await session.execute(select(UserRoleModel))
        existing_roles = {role.name.value for role in result_roles.scalars().all()}
        
        for role in UserRoleEnum:
            if role.value not in existing_roles:
                new_role = UserRoleModel(name=role)
                session.add(new_role)
        
        result_user = await session.execute(select(UserModel).filter(UserModel.email == "admin@gmail.com"))
        existing_user = result_user.scalars().first()
        
        if not existing_user:
            admin_role = await session.execute(select(UserRoleModel).filter(UserRoleModel.name == UserRoleEnum.ADMIN.value))
            admin_role = admin_role.scalars().first()
            
            new_user = UserModel.create(
                email="admin@gmail.com",
                raw_password="ZXCzxc!@#123",
            )
            new_user.role_id = admin_role.id
            session.add(new_user)
        
        await session.commit()
