"""ROI arithmetic and the admin input-rounding policy."""

from dataclasses import dataclass

from exceptions.roi import NegativeROIError


@dataclass(frozen=True)
class ROICalculation:
    roi: float
    profit_margin: float


def profit_margin_for_roi(value: float) -> float:
    return round(100 - (10000 / (value + 100)) if value + 100 != 0 else 0, 2)


def normalize_admin_roi(value: float) -> float:
    if value < 0:
        raise NegativeROIError("ROI must be greater than or equal to 0")
    return round(value, 2)


def calculate_admin_roi(value: float) -> ROICalculation:
    normalized = normalize_admin_roi(value)
    return ROICalculation(normalized, profit_margin_for_roi(normalized))
