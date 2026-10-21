import pytest

from domain.roi import calculate_admin_roi, normalize_admin_roi, profit_margin_for_roi
from exceptions.roi import NegativeROIError
from models.admin import ROIModel


@pytest.mark.parametrize(
    "roi,margin",
    [
        (-200, 200),
        (-100, 0),
        (-50, -100),
        (0, 0),
        (1, 0.99),
        (25, 20),
        (50, 33.33),
        (100, 50),
    ],
)
def test_formula_retains_rounding_and_zero_denominator_policy(roi, margin):
    assert profit_margin_for_roi(roi) == margin


@pytest.mark.parametrize("value,normalized,margin", [(0, 0, 0), (25.004, 25, 20), (2.675, 2.67, 2.6)])
def test_admin_calculation_rounds_roi_before_margin(value, normalized, margin):
    result = calculate_admin_roi(value)
    assert result.roi == normalized and result.profit_margin == margin


@pytest.mark.parametrize("value", [-1, -0.001, -100])
def test_negative_values_are_rejected_before_rounding(value):
    with pytest.raises(NegativeROIError, match="ROI must be greater than or equal to 0"):
        normalize_admin_roi(value)


def test_missing_query_value_retains_type_error_for_http_mapping():
    with pytest.raises(TypeError):
        calculate_admin_roi(None)


def test_model_keeps_its_separate_nullable_and_negative_roi_policy():
    assert ROIModel(roi=-100).profit_margin == 0
    model = ROIModel(roi=25)
    model.roi = None
    assert model.profit_margin == 20
