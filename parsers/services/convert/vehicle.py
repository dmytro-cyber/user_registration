import os
import json
import logging
from datetime import datetime
from typing import Dict, Any

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Вивід до консолі
        # Додайте logging.FileHandler('data_processing.log') для логування у файл, якщо потрібно
    ]
)
logger = logging.getLogger(__name__)

def str_to_bool(value: str) -> bool:
    """Convert 'Yes'/'No' string to boolean."""
    return value.lower() == "yes"

def is_salvage_from_document(document: str) -> bool:
    """Convert document field to boolean is_salvage."""
    return document.lower() == "salvage"

def parse_auction_date(date_str: str) -> datetime:
    """Parse ISO 8601 date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")

def format_car_data(api_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format API response to match CarModel structure.

    Args:
        api_response: JSON response from the API.

    Returns:
        Dict matching CarModel fields and types.
    """
    logger.info("Starting to format car data from API response")

    field_mapping = {
        "vin": "vin",
        "title": "vehicle",
        "fuel": "fuel_type",
        "make": "make",
        "model": "model",
        "year": "year",
        "odometer": "mileage",
        "base_site": "auction",
        "auction_type": "auction_name",
        "auction_date": "date",
        "lot_id": "lot",
        "seller": "seller",
        "link": "link",
        "location": "location",
        "engine": "engine_title",
        "current_bid": "current_bid",
        "engine_size": "engine",
        "keys": "has_keys",
        "cylinders": "engine_cylinder",
        "drive": "drive_type",
        "color": "exterior_color",
        "body_type": "body_style",
        "transmission": "transmision",
        "vehicle_type": "vehicle_type",
    }

    type_conversions = {
        "date": parse_auction_date,
        "bid": float,
        "engine": float,
        "has_keys": str_to_bool,
        "is_salvage": is_salvage_from_document,
    }

    car_data = {}

    for api_field, model_field in field_mapping.items():
        if api_field in api_response:
            value = api_response[api_field]
            if value is not None:
                try:
                    if model_field in type_conversions:
                        car_data[model_field] = type_conversions[model_field](value)
                        logger.debug(f"Converted {model_field} from {value} to {car_data[model_field]}")
                    else:
                        car_data[model_field] = value
                        logger.debug(f"Set {model_field} to {value}")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to convert {model_field} from {value}: {e}")
                    car_data[model_field] = value  # Зберігаємо як є у випадку помилки

    car_data.setdefault("has_correct_vin", False)
    car_data.setdefault("has_correct_owners", False)
    car_data.setdefault("has_correct_accidents", False)
    car_data.setdefault("has_correct_mileage", False)
    logger.debug("Set default values for required fields")

    optional_fields = [
        "owners", "accident_count", "actual_bid", "price_sold", "suggested_bid",
        "total_investment", "net_profit", "profit_margin", "roi", "parts_cost",
        "maintenance", "auction_fee", "transportation", "labor", "parts_needed",
        "predicted_roi", "predicted_profit_margin", "interior_color", "style_id",
    ]
    for field in optional_fields:
        car_data.setdefault(field, None)
    logger.debug(f"Set default None for optional fields: {optional_fields}")

    if "document" in api_response:
        car_data["is_salvage"] = is_salvage_from_document(api_response["document"])
        logger.debug(f"Set is_salvage to {car_data['is_salvage']} based on document")

    car_data["parts"] = []
    car_data["sales_history"] = []
    logger.debug("Initialized parts and sales_history as empty lists")

    car_data["photos"] = [{"url": url} for url in api_response.get("link_img_small", [])]
    car_data["photos_hd"] = [{"url": url} for url in api_response.get("link_img_hd", [])]
    logger.debug(f"Processed photos: {len(car_data['photos'])} small, {len(car_data['photos_hd'])} HD")

    if api_response.get("sale_history"):
        car_data["sales_history"] = [
            {
                "date": parse_auction_date(item["sale_date"]),
                "source": item["base_site"],
                "lot_number": item["lot_id"],
                "final_bid": item["purchase_price"],
                "status": item["sale_status"],
            }
            for item in api_response["sale_history"]
        ]
        logger.debug(f"Processed {len(car_data['sales_history'])} sales history entries")
    else:
        logger.debug("No sales history found in API response")

    # Condition Assessment
    condition_assessments = []
    if "damage_pr" in api_response:
        condition_assessments.append({"type_of_damage": "damage_pr", "issue_description": api_response["damage_pr"]})
    if "damage_sec" in api_response:
        condition_assessments.append({"type_of_damage": "damage_sec", "issue_description": api_response["damage_sec"]})
    car_data["condition_assessments"] = condition_assessments
    logger.debug(f"Processed {len(condition_assessments)} condition assessments")

    logger.info("Successfully formatted car data")
    return car_data