import csv
import http.client
import json
import os
from datetime import date, datetime
from difflib import SequenceMatcher
from math import atan2, cos, radians, sin, sqrt

from passlib.context import CryptContext
from sqlalchemy import and_, distinct, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.session import SessionLocal
from models import CarModel, UserModel, UserRoleEnum, UserRoleModel, USZipModel
from models.user import UserRoleEnum, UserRoleModel
from models.vehicle import CarModel, PartModel

EARTH_RADIUS_MI = 3958.8

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def import_us_zips_from_csv(csv_path: str = "uszips.csv"):
    async with SessionLocal() as session:
        with open(csv_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            objects = []

            for row in reader:
                try:
                    zip_code = row["zip"].strip()
                    lat = float(row["lat"])
                    lng = float(row["lng"])
                    city = row["city"].strip()
                    state_id = row["state_id"].strip()
                    state_name = row["state_name"].strip()

                    zip_obj = USZipModel(
                        zip=zip_code,
                        lat=lat,
                        lng=lng,
                        city=city,
                        state_id=state_id,
                        state_name=state_name,
                        copart_name=None,
                        iaai_name=None,
                    )
                    objects.append(zip_obj)
                except Exception as e:
                    print(f"⚠️  Skipped: {e}")

            session.add_all(objects)
            await session.commit()
            print(f"✅ Created {len(objects)} ZIPs")


async def create_roles():
    async with SessionLocal() as session:
        result_roles = await session.execute(select(UserRoleModel))
        existing_roles = {role.name.value for role in result_roles.scalars().all()}

        for role in UserRoleEnum:
            if role.value not in existing_roles:
                new_role = UserRoleModel(name=role)
                session.add(new_role)

        result_user = await session.execute(select(UserModel).filter(UserModel.email == os.getenv("ADMIN_USERNAME")))
        existing_user = result_user.scalars().first()

        if not existing_user:
            admin_role = await session.execute(
                select(UserRoleModel).filter(UserRoleModel.name == UserRoleEnum.ADMIN.value)
            )
            admin_role = admin_role.scalars().first()

            new_user = UserModel.create(
                email=os.getenv("ADMIN_USERNAME"),
                raw_password=os.getenv("ADMIN_PASSWORD"),
            )
            new_user.first_name = "Hansel"
            new_user.last_name = "Castillo"
            new_user.phone_number = "admin"
            new_user.date_of_birth = date.today()
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
        return (
            float(value.replace("$", "").replace(",", ".").replace(" ", "")) if value and value != "#DIV/0!" else None
        )
    except ValueError:
        return None


# async def import_cars_from_csv(csv_file):
#     async with SessionLocal() as session:
#         result = await session.execute(select(CarModel))
#         cars = result.scalars().all()
#         if not cars:
#             with open(csv_file, newline="", encoding="utf-8") as file:
#                 reader = csv.DictReader(file)
#                 for row in reader:
#                     try:
#                         car = CarModel(
#                             vin=row["VIN"].strip(),
#                             vehicle=row["Vehicle"].strip(),
#                             mileage=safe_int(row["Miles"]),
#                             auction=row["Auction"].strip(),
#                             auction_name=row["Auction Name"].strip(),
#                             date=datetime.strptime(row["DATE"], "%m/%d/%y") if row["DATE"] else None,
#                             lot=safe_int(row["Lot"]),
#                             seller=row["Seller"].strip(),
#                             owners=safe_int(row["Owners"]),
#                             accident_count=safe_int(row["Accident"]),
#                             bid=safe_float(row["C/ Bid"]),
#                             actual_bid=safe_float(row["Act Bid"]),
#                             price_sold=safe_float(row["Price Sold"]),
#                             suggested_bid=safe_float(row["Sug Bid"]),
#                             total_investment=safe_float(row["Total Investment"]),
#                             net_profit=safe_float(row["Net Profit"]),
#                             profit_margin=safe_float(row["Profit Margin %"]),
#                             roi=safe_float(row["ROI"]),
#                             maintenance=safe_float(row["Maintenance"]),
#                             auction_fee=safe_float(row["Auction Fee"]),
#                             transportation=safe_float(row["Transportation"]),
#                             labor=safe_float(row["Labor"]),
#                             is_salvage=True if row["Chismoso"].strip().upper() == "TRUE" else False,
#                             parts_cost=safe_float(row["Parts"]),
#                             parts_needed=row["Parts Needed"].strip() if row["Parts Needed"] else None,
#                         )
#                         session.add(car)
#                         await session.commit()
#                         await session.refresh(car)

#                         for i in range(1, 21):
#                             part_name = row.get(f"Part {i}", "").strip()
#                             part_value = row.get(f"Value {i}", "").strip()
#                             if part_name and part_value:
#                                 part = PartModel(
#                                     name=part_name,
#                                     value=float(part_value.replace(",", ".")) if part_value else None,
#                                     car_id=car.id,
#                                 )
#                                 session.add(part)

#                         await session.commit()
#                     except Exception:
#                         pass


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return EARTH_RADIUS_MI * c


def get_location_coordinates(query):
    conn = http.client.HTTPSConnection("google.serper.dev")
    payload = json.dumps({"q": query, "location": "United States"})
    headers = {"X-API-KEY": "d9b2f54b9bed2611b48823eb727651c67575cfea", "Content-Type": "application/json"}
    conn.request("POST", "/places", payload, headers)
    res = conn.getresponse()
    data = res.read()
    data_dict = json.loads(data.decode("utf-8"))
    places = data_dict.get("places", [])
    if places:
        try:
            return places[0]["latitude"], places[0]["longitude"]
        except Exception:
            pass
    return None, None


def similar(a, b):
    """Порівнює схожість між назвами"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


async def match_and_update_location(location: str, auction: str):
    async with SessionLocal() as db:
        lat, lon = get_location_coordinates(location)
        if not lat or not lon:
            return

        zip_stmt = await db.execute(select(USZipModel))
        all_zips = zip_stmt.scalars().all()

        if all_zips:
            for zip_entry in all_zips:
                dist = haversine(lat, lon, float(zip_entry.lat), float(zip_entry.lng))
                if dist <= 20:
                    if similar(zip_entry.city, location) > 0.8 and zip_entry.state_id.lower() in location.lower():
                        if auction.lower() == "copart" and not zip_entry.copart_name:
                            zip_entry.copart_name = location
                        elif auction.lower() == "iaai" and not zip_entry.iaai_name:
                            zip_entry.iaai_name = location

            await db.commit()


async def match_and_update_locations():
    async with SessionLocal() as db:
        stmt = (
            select(CarModel.location, CarModel.auction)
            .distinct()
            .where(and_(CarModel.location.isnot(None), CarModel.auction.isnot(None)))
        )
        result = await db.execute(stmt)
        rows = result.all()

        for location, auction in rows:
            lat, lon = get_location_coordinates(location)
            if not lat or not lon:
                continue

            zip_stmt = await db.execute(select(USZipModel))
            all_zips = zip_stmt.scalars().all()

            if all_zips:
                for zip_entry in all_zips:
                    dist = haversine(lat, lon, float(zip_entry.lat), float(zip_entry.lng))
                    if dist <= 20:
                        if similar(zip_entry.city, location) > 0.8 and zip_entry.state_id.lower() in location.lower():
                            if auction.lower() == "copart" and not zip_entry.copart_name:
                                zip_entry.copart_name = location
                            elif auction.lower() == "iaai" and not zip_entry.iaai_name:
                                zip_entry.iaai_name = location

                await db.commit()
