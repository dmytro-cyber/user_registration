from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.user import UserRoleEnum, UserRoleModel
from db.session import SessionLocal
from sqlalchemy.orm import selectinload
from passlib.context import CryptContext
from models import UserModel, UserRoleModel, UserRoleEnum
from sqlalchemy.exc import IntegrityError
import csv
from datetime import datetime
from models.vehicle import Car, Part

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
            admin_role = await session.execute(
                select(UserRoleModel).filter(UserRoleModel.name == UserRoleEnum.ADMIN.value)
            )
            admin_role = admin_role.scalars().first()

            new_user = UserModel.create(
                email="admin@gmail.com",
                raw_password="ZXCzxc!@#123",
            )
            new_user.role_id = admin_role.id
            session.add(new_user)

        await session.commit()


def safe_int(value):
    try:
        return int(value.replace(",", "")) if value else None
    except ValueError:
        return None


def safe_float(value):
    try:
        return float(value.replace("$", "").replace(",", ".").replace(" ", "")) if value and value != '#DIV/0!' else None
    except ValueError:
        return None


async def import_cars_from_csv(csv_file):
    async with SessionLocal() as session:
        result = await session.execute(select(Car))
        cars = result.scalars().all()
        if not cars:
            with open(csv_file, newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    car = Car(
                        vin=row["VIN"].strip(),
                        vehicle=row["Vehicle"].strip(),
                        mileage=safe_int(row["Miles"]),
                        auction=row["Auction"].strip(),
                        auction_name=row["Auction Name"].strip(),
                        date=datetime.strptime(row["DATE"], "%m/%d/%y") if row["DATE"] else None,
                        lot=safe_int(row["Lot"]),
                        seller=row["Seller"].strip(),
                        owners=safe_int(row["Owners"]),
                        accident_count=safe_int(row["Accident"]),
                        bid=safe_float(row["C/ Bid"]),
                        actual_bid=safe_float(row["Act Bid"]),
                        price_sold=safe_float(row["Price Sold"]),
                        suggested_bid=safe_float(row["Sug Bid"]),
                        total_investment=safe_float(row["Total Investment"]),
                        net_profit=safe_float(row["Net Profit"]),
                        profit_margin=safe_float(row["Profit Margin %"]),
                        roi=safe_float(row["ROI"]),
                        maintenance=safe_float(row["Maintenance"]),
                        auction_fee=safe_float(row["Auction Fee"]),
                        transportation=safe_float(row["Transportation"]),
                        labor=safe_float(row["Labor"]),
                        is_salvage=True if row["Chismoso"].strip().upper() == "TRUE" else False,
                        parts_cost=safe_float(row["Parts"]),
                        parts_needed=row["Parts Needed"].strip() if row["Parts Needed"] else None,
                    )
                    session.add(car)
                    await session.commit()
                    await session.refresh(car)

                    for i in range(1, 21):
                        part_name = row.get(f"Part {i}", "").strip()
                        part_value = row.get(f"Value {i}", "").strip()
                        if part_name and part_value:
                            part = Part(
                                name=part_name,
                                value=float(part_value.replace(",", ".")) if part_value else None,
                                car_id=car.id,
                            )
                            session.add(part)

                    await session.commit()
