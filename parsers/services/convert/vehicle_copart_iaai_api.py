import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def str_to_bool(value: str) -> bool:
    """Convert 'Yes'/'No'/'yes'/'no' string to boolean."""
    return value.strip().lower() in {"yes", "y", "true", "1"}


def is_salvage_from_document(document: str | None) -> bool:
    """Return True if document/doc_type contains 'salvage' (case-insensitive)."""
    if not document:
        return False
    return "salvage" in document.lower()


def parse_cylinders(value: str | None) -> Optional[int]:
    """Parse number of cylinders from string like '4 Cyl'."""
    if not value:
        return None
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def parse_auction_date(
    s: Any,
    default_tz: str | None = "UTC",
    to_utc: bool = True,
) -> Optional[datetime]:
    """
    Returns the timezone-aware datetime.

    Supports:
    - int/float: Unix timestamp (sec or ms)
    - digits-only string: Unix timestamp or 'YYYYMMDD'
    - ISO strings with or without TZ (Z or ±HH:MM)
    - Fallback legacy formats.
    """
    if s is None or s == "":
        return None

    # 1) Unix timestamps as int/float
    if isinstance(s, (int, float)):
        ts = float(s)
        if ts > 1e12:  # ms -> sec
            ts /= 1000.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt if not to_utc else dt.astimezone(timezone.utc)

    raw = str(s).strip()

    # 2) Digits-only strings: epoch or YYYYMMDD
    if raw.isdigit():
        if len(raw) >= 10:  # epoch sec/ms
            ts = int(raw)
            if ts > 1_000_000_000_000:  # ms
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt if not to_utc else dt.astimezone(timezone.utc)
        if len(raw) == 8:  # YYYYMMDD
            try:
                dt = datetime.strptime(raw, "%Y%m%d")
                if default_tz is not None:
                    dt = dt.replace(tzinfo=ZoneInfo(default_tz))
                return dt.astimezone(timezone.utc) if to_utc else dt
            except ValueError:
                pass  # fall through to ISO parsing

    # 3) ISO-like strings
    try:
        iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(iso)
    except ValueError:
        dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None

    if dt.tzinfo is None:
        if default_tz is None:
            return None
        dt = dt.replace(tzinfo=ZoneInfo(default_tz))

    return dt.astimezone(timezone.utc) if to_utc else dt


def _extract_engine_displacement(engine_type: str | None) -> Optional[float]:
    """Extract engine displacement in liters from string like '2.0L I-4 ...'."""
    if not engine_type:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*L", engine_type, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_iaai_lot_from_url(url: str) -> Optional[str]:
    """
    Спроба витягнути IAAI lot id з URL виду:
    https://vis.iaai.com/resizer?imageKeys=43777578~SID~B342~S0~I1~...
    Повертає "43777578" або None.
    """
    if "iaai.com" not in url:
        return None

    # Спробуємо по параметру imageKeys=12345...
    m = re.search(r"imageKeys=([^&]+)", url)
    if not m:
        return None

    value = m.group(1)
    # Беремо лише перший блок до ~ і тільки цифри
    m2 = re.match(r"(\d+)", value)
    if not m2:
        return None
    return m2.group(1)


def format_car_data(api_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format API response (old or new) to match CarModel structure.

    Works with:
    - старий формат (document, damage_pr, damage_sec, sale_history, link_img_*)
    - новий формат (doc_type, primary_damage, secondary_damage, sales_history,
      car_photo, active_bidding, buy_now_car, lot_number, etc.)
    """

    field_mapping = {
        # базові поля (спільні / старий формат)
        "vin": "vin",
        "title": "vehicle",  # старий формат
        "fuel": "fuel_type",
        "make": "make",
        "model": "model",
        "year": "year",
        "odometer": "mileage",
        "auction_name": "auction",        # для обох форматів
        "auction_type": "auction_name",   # старий формат
        "auction_date": "date",           # старий формат
        "lot_id": "lot",                  # старий формат
        "lot_number": "lot",              # новий формат
        "seller": "seller",
        "seller_type": "seller_type",
        "link": "link",
        "location": "location",
        "engine_type": "engine_title",
        "highlights": "condition",
        "current_bid": "current_bid",     # якщо прийде зверху
        "car_keys": "has_keys",
        "cylinders": "engine_cylinder",
        "drive": "drive_type",
        "color": "exterior_color",
        "body_style": "body_style",
        "transmission": "transmision",
        "vehicle_type": "vehicle_type",

        # документ для salvage
        "document": "document",  # старий формат
        "doc_type": "document",  # новий формат
    }

    type_conversions = {
        "date": parse_auction_date,
        "engine": float,               # лишаємо для сумісності зі старим 'engine_size'
        "has_keys": str_to_bool,
        "engine_cylinder": parse_cylinders,
        # is_salvage рахуємо окремо з document/doc_type
    }

    car_data: Dict[str, Any] = {}

    # --- маппінг простих полів ---
    for api_field, model_field in field_mapping.items():
        if api_field in api_response:
            value = api_response[api_field]
            if value is not None:
                try:
                    if model_field in type_conversions:
                        car_data[model_field] = type_conversions[model_field](value)
                        logger.debug(
                            "Converted %s from %r to %r",
                            model_field,
                            value,
                            car_data[model_field],
                        )
                    else:
                        car_data[model_field] = value
                        logger.debug("Set %s to %r", model_field, value)
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Failed to convert %s from %r: %s", model_field, value, e
                    )
                    car_data[model_field] = value

    # --- двигун: float + повна назва + кількість циліндрів ---
    engine_title = api_response.get("engine_type") or car_data.get("engine_title")
    if engine_title:
        car_data["engine_title"] = engine_title
        engine_displacement = _extract_engine_displacement(engine_title)
        if engine_displacement is not None:
            car_data["engine"] = engine_displacement

    # engine_cylinder уже прогнали через parse_cylinders; якщо ні – спробуємо ще раз:
    if "engine_cylinder" not in car_data:
        car_data["engine_cylinder"] = parse_cylinders(api_response.get("cylinders"))

    # --- VIN/owners/mileage correctness flags ---
    car_data.setdefault("has_correct_vin", False)
    car_data.setdefault("has_correct_owners", False)
    car_data.setdefault("has_correct_accidents", False)
    car_data.setdefault("has_correct_mileage", False)

    # --- опціональні поля (як і було) ---
    optional_fields = [
        "owners",
        "accident_count",
        "actual_bid",
        "price_sold",
        "suggested_bid",
        "total_investment",
        "net_profit",
        "profit_margin",
        "roi",
        "parts_cost",
        "maintenance",
        "auction_fee",
        "transportation",
        "labor",
        "parts_needed",
        "predicted_roi",
        "predicted_profit_margin",
        "interior_color",
        "style_id",
    ]
    for field in optional_fields:
        car_data.setdefault(field, None)

    # --- is_salvage з document або doc_type ---
    document_value = (
        api_response.get("document")
        or api_response.get("doc_type")
        or car_data.get("document")
    )
    car_data["is_salvage"] = is_salvage_from_document(document_value)

    # --- дата аукціону (якщо не прийшла напряму) ---
    if "date" not in car_data or car_data["date"] is None:
        auction_date_raw: Any = None

        active_bidding = api_response.get("active_bidding") or []
        if active_bidding:
            auction_date_raw = active_bidding[0].get("sale_date")

        if auction_date_raw is None and api_response.get("buy_now_car"):
            auction_date_raw = api_response["buy_now_car"].get("sale_date")

        sales_history_last = api_response.get("sales_history_last")
        if auction_date_raw is None and sales_history_last:
            auction_date_raw = sales_history_last.get("sale_date")

        sales_history_list = api_response.get("sales_history") or api_response.get(
            "sale_history"
        )
        if auction_date_raw is None and sales_history_list:
            auction_date_raw = sales_history_list[0].get("sale_date")

        if auction_date_raw is not None:
            car_data["date"] = parse_auction_date(auction_date_raw)

    # --- current_bid з active_bidding, якщо немає ---
    if "current_bid" not in car_data or car_data["current_bid"] is None:
        active_bidding = api_response.get("active_bidding") or []
        if active_bidding:
            car_data["current_bid"] = active_bidding[0].get("current_bid", 0)

    # --- title/vehicle, якщо не прийшло окремо ---
    if not car_data.get("vehicle"):
        parts = []
        if car_data.get("year"):
            parts.append(str(car_data["year"]))
        if car_data.get("make"):
            parts.append(str(car_data["make"]))
        if car_data.get("model"):
            parts.append(str(car_data["model"]))
        if api_response.get("series"):
            parts.append(str(api_response["series"]))
        if parts:
            car_data["vehicle"] = " ".join(parts)

    # --- photos + photos_hd (однакові посилання, як ти просив) ---
    small_photos = api_response.get("link_img_small") or []
    hd_photos = api_response.get("link_img_hd") or []

    if not small_photos and "car_photo" in api_response:
        small_photos = api_response["car_photo"].get("photo", []) or []

    hd_photos = small_photos

    car_data["photos"] = [{"url": url} for url in small_photos]
    car_data["photos_hd"] = [{"url": url} for url in hd_photos]

    # --- IAAI link з photo (vis.iaai.com/resizer?imageKeys=...) ---
    iaai_lot_id: Optional[str] = None
    for url in small_photos + hd_photos:
        iaai_lot_id = _extract_iaai_lot_from_url(url)
        if iaai_lot_id:
            break

    if iaai_lot_id and not car_data.get("link"):
        car_data["link"] = f"https://www.iaai.com/VehicleDetail/{iaai_lot_id}~US"
    else:
        car_data["link"] = f"https://www.copart.com/lot/{api_response.get("lot_number")}"

    # --- sales_history (старий + новий формат, у т.ч. як у твоєму COPART JSON) ---
    car_data["sales_history"] = []
    raw_sales_history = api_response.get("sale_history") or api_response.get("sales_history")

    if raw_sales_history:
        for item in raw_sales_history:
            # sale_date може бути epoch (int) або строкою — parse_auction_date все це підтримує
            sale_date = item.get("sale_date")

            # lot_number пріорітетно беремо з item, потім з кореня, потім all_lots_id/id як fallback
            lot_number = (
                item.get("lot_number")
                or api_response.get("lot_number")
                or api_response.get("lot_id")
                or item.get("all_lots_id")
                or item.get("id")
            )

            auction_info = item.get("auction_info") or {}

            # source: auction_code / country_name / base_site / auction_name
            source_raw = (
                auction_info.get("auction_code")
                or auction_info.get("country_name")
                or item.get("base_site")
                or api_response.get("auction_name")
            )

            source = "IAAI" if source_raw == 1 else "Copart"

            car_data["sales_history"].append(
                {
                    "date": parse_auction_date(sale_date),
                    "source": source,
                    "lot_number": lot_number,
                    "final_bid": item.get("purchase_price"),
                    "status": item.get("sale_status"),
                }
            )

    # --- condition_assessments: залишаємо старий формат type_of_damage ---
    condition_assessments = []
    primary_damage = api_response.get("damage_pr") or api_response.get("primary_damage")
    secondary_damage = api_response.get("damage_sec") or api_response.get(
        "secondary_damage"
    )

    if primary_damage:
        condition_assessments.append(
            {"type_of_damage": "damage_pr", "issue_description": primary_damage}
        )
    if secondary_damage:
        condition_assessments.append(
            {"type_of_damage": "damage_sec", "issue_description": secondary_damage}
        )

    car_data["condition_assessments"] = condition_assessments

    # --- parts (поки порожній список, як і було) ---
    car_data.setdefault("parts", [])
    logger.debug(
        "Formatted car_data for VIN %s", car_data.get("vin", "<unknown>")
    )

    return car_data
