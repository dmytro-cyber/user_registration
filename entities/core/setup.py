import csv
import http.client
import json
import os
from datetime import date, datetime
from difflib import SequenceMatcher
from math import atan2, cos, radians, sin, sqrt
from typing import Dict

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


async def import_us_zips_from_csv(
    csv_path: str = "uszips.csv",
    iaai_path: str = "locations_iaai_standardized.json",
    copart_path: str = "locations_copart_standardized.json",
):
    """
    Import/refresh US ZIP records from a CSV and enrich them with IAAI/Copart location names.

    Workflow:
      1) Read `uszips.csv` and upsert basic ZIP info (zip, lat, lng, city, state_id, state_name).
      2) Read two JSON files mapping ZIP -> location name:
         - `locations_iaai_standardized.json` for IAAI (sets `iaai_name`)
         - `locations_copart_standardized.json` for Copart (sets `copart_name`)
      3) For matching ZIPs in DB, update `iaai_name` / `copart_name`.

    JSON format supported:
      - Flat map: {"12345": "Some Location", ...}
      - Or nested under a known root: {"iaai": {...}} / {"copart": {...}} / {"data": {...}}

    Args:
        csv_path: Path to the ZIP CSV file.
        iaai_path: Path to the IAAI ZIP->name JSON.
        copart_path: Path to the Copart ZIP->name JSON.
    """

    def _read_json_map(path: str, root_keys: tuple[str, ...]) -> Dict[str, str]:
        """
        Read a JSON file and return a ZIP->name mapping.

        Supports either:
          - a flat dict: {"12345": "Name"}
          - a dict with a root key that contains the map, e.g. {"iaai": {...}}.

        Returns:
            Dict[str, str]: Normalized ZIP->name map. Empty dict if file missing/invalid.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            print(f"⚠️  JSON not found: {path} — skipping.")
            return {}
        except Exception as e:
            print(f"⚠️  Failed to read {path}: {e} — skipping.")
            return {}

        if isinstance(raw, dict):
            # Nested root?
            for k in root_keys:
                if k in raw and isinstance(raw[k], dict):
                    return {str(z).strip(): str(name).strip() for z, name in raw[k].items()}
            # Flat dict?
            if all(isinstance(k, str) for k in raw.keys()):
                return {str(z).strip(): str(name).strip() for z, name in raw.items()}

        print(f"⚠️  Unexpected JSON structure in {path}.")
        return {}

    created, updated = 0, 0
    iaai_updates, copart_updates = 0, 0

    async with SessionLocal() as session:
        # 1) CSV upsert of ZIP base data
        with open(csv_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                try:
                    zip_code = row["zip"].strip()
                    lat = float(row["lat"])
                    lng = float(row["lng"])
                    city = row["city"].strip()
                    state_id = row["state_id"].strip()
                    state_name = row["state_name"].strip()

                    # Check if the ZIP already exists
                    res = await session.execute(
                        select(USZipModel).where(USZipModel.zip == zip_code)
                    )
                    obj = res.scalars().first()

                    if obj:
                        # Update base fields (in case CSV has newer data)
                        obj.lat = lat
                        obj.lng = lng
                        obj.city = city
                        obj.state_id = state_id
                        obj.state_name = state_name
                        updated += 1
                    else:
                        obj = USZipModel(
                            zip=zip_code,
                            lat=lat,
                            lng=lng,
                            city=city,
                            state_id=state_id,
                            state_name=state_name,
                            copart_name=None,
                            iaai_name=None,
                        )
                        session.add(obj)
                        created += 1

                except Exception as e:
                    print(f"⚠️  Skipped CSV row: {e}")

        await session.commit()
        print(f"✅ CSV: created={created}, updated={updated}")

        # 2) JSON enrichment: IAAI / Copart names
        iaai_map: Dict[str, str] = _read_json_map(iaai_path, root_keys=("iaai", "data"))
        copart_map: Dict[str, str] = _read_json_map(copart_path, root_keys=("copart", "data"))

        # Apply IAAI names
        if iaai_map:
            res = await session.execute(
                select(USZipModel).where(USZipModel.zip.in_(list(iaai_map.keys())))
            )
            rows = res.scalars().all()
            for row in rows:
                row.iaai_name = iaai_map.get(row.zip)
                iaai_updates += 1

        # Apply Copart names
        if copart_map:
            res = await session.execute(
                select(USZipModel).where(USZipModel.zip.in_(list(copart_map.keys())))
            )
            rows = res.scalars().all()
            for row in rows:
                row.copart_name = copart_map.get(row.zip)
                copart_updates += 1

        await session.commit()
        print(f"✅ JSON: iaai_updates={iaai_updates}, copart_updates={copart_updates}")


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
    headers = {"X-API-KEY": "bab5fa7203c8e6eeaaacbf0041782fdc5dab1e59", "Content-Type": "application/json"}
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
