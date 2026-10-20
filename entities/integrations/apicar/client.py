"""APICAR access and the established vehicle projection shared by API and jobs."""

import os

import httpx

from integrations.apicar.conversion import format_car_data

BASE_URL = "https://api.apicar.store/api"


def headers():
    return {"api-key": os.getenv("APICAR_KEY")}


def project_vehicle(data):
    fields = (
        "vin",
        "vehicle",
        "year",
        "mileage",
        "auction",
        "auction_name",
        "lot",
        "seller",
        "location",
        "bid",
        "engine",
        "has_keys",
        "engine_cylinder",
        "drive_type",
        "exterior_color",
        "body_style",
        "transmision",
        "vehicle_type",
    )
    result = {field: data.get(field) for field in fields}
    result["vin"] = data["vin"]
    result.update(
        date=data["date"].isoformat() if data.get("date") else None,
        is_salvage=data.get("is_salvage", False),
        photos=data.get("photos", []),
        sales_history=data.get("sales_history", []),
        current_bid=data.get("current_bid", 0),
    )
    return result


def adapt_vehicle(response, update=False):
    raw = response.json()
    return project_vehicle(format_car_data(raw[0] if update else raw.get("data")))


class ApicarVehicleNotFound(Exception):
    pass


def get_vehicle(vin, *, update=False):
    path = "/cars/vin/all" if update else "/sale-histories/vin"
    try:
        response = httpx.get(f"{BASE_URL}{path}?vin={vin}", timeout=10, headers=headers())
        response.raise_for_status()
    except httpx.HTTPError:
        if update:
            raise ApicarVehicleNotFound(vin)
        return None
    return adapt_vehicle(response, update)


async def get_vehicle_async(vin, *, update=False):
    path = "/cars/vin/all" if update else "/sale-histories/vin"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{BASE_URL}{path}?vin={vin}", headers=headers())
            response.raise_for_status()
    except httpx.HTTPError:
        if update:
            raise ApicarVehicleNotFound(vin)
        return None
    return adapt_vehicle(response, update)


def _lots(items):
    return [{"lot_id": item["lot"], "site": 1 if "copart" in item["source"].lower() else 2} for item in items]


def current_bids(items):
    lots = _lots(items)
    result = []
    with httpx.Client(timeout=30) as client:
        for offset in range(0, len(lots), 20):
            try:
                response = client.post(
                    f"{BASE_URL}/cars/current-bid/many",
                    headers={
                        **headers(),
                        "Content-Type": "application/json",
                        "User-Agent": "insomnia/11.3.0",
                    },
                    json={"lots": lots[offset : offset + 20]},
                )
                response.raise_for_status()
                result.extend(response.json())
            except httpx.HTTPError:
                continue
    return {"bids": result}


async def current_bids_async(items):
    lots = _lots(items)
    result = []
    async with httpx.AsyncClient(timeout=30) as client:
        for offset in range(0, len(lots), 20):
            try:
                response = await client.post(
                    f"{BASE_URL}/cars/current-bid/many",
                    headers={
                        **headers(),
                        "Content-Type": "application/json",
                        "User-Agent": "insomnia/11.3.0",
                    },
                    json={"lots": lots[offset : offset + 20]},
                )
                response.raise_for_status()
                result.extend(response.json())
            except httpx.HTTPError:
                continue
    return {"bids": result}
