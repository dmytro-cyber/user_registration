import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

AUDIT_DIR = Path("audit_logs")
AUDIT_DIR.mkdir(exist_ok=True)


def _today_file() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"car_updates_{today}.jsonl"


def _serialize(value: Any):
    """Make values JSON serializable."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _model_to_dict(model) -> Dict[str, Any]:
    """Convert SQLAlchemy model to dict."""
    return {
        column.name: _serialize(getattr(model, column.name))
        for column in model.__table__.columns
    }


async def log_car_update(before_model, after_model):
    """
    Append car update record to JSONL file.
    """

    before_data = _model_to_dict(before_model) if before_model else None
    after_data = _model_to_dict(after_model)

    # Skip if identical
    if before_data == after_data:
        return

    record = {
        "vin": after_data.get("vin"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "before": before_data,
        "after": after_data,
    }

    file_path = _today_file()

    loop = asyncio.get_running_loop()

    def _write():
        with file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    await loop.run_in_executor(None, _write)
