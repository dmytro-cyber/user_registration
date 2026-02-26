from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
import json


AUDIT_DIR = Path("audit_logs")
AUDIT_DIR.mkdir(exist_ok=True)


def _today_file() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"car_updates_{today}.jsonl"


def _serialize(value: Any):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()

    if hasattr(value, "value"):  # Enum support
        return value.value

    return value


def _model_to_dict(model) -> Dict[str, Any]:
    return {
        column.name: _serialize(getattr(model, column.name))
        for column in model.__table__.columns
    }


def _calculate_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    diff = {}

    for key in after.keys():
        before_val = before.get(key)
        after_val = after.get(key)

        if before_val != after_val:
            diff[key] = {
                "before": before_val,
                "after": after_val,
            }

    return diff


async def log_car_update(before_data, after_model):

    try:
        after_data = _model_to_dict(after_model)

        diff = _calculate_diff(before_data, after_data)

        if not diff:
            return

        record = {
            "vin": after_data.get("vin"),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "changes": diff,
        }

        file_path = _today_file()

        with file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:
        logger.exception("Audit log failed")