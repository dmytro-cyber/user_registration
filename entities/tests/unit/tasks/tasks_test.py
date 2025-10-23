# entities/tests/unit/tasks/tasks_test.py
from datetime import datetime

import pytest

from models.vehicle import AutoCheckModel, CarModel, RecommendationStatus, RelevanceStatus


class _RespBase:
    status_code = 200
    def raise_for_status(self): return None
    def json(self): return {}

def test_happy_path_math_and_invocations(
    patch_task_sessionlocal,
    patch_task_settings,
    mock_s3_for_task,
    mock_roi_and_fees,
    http_router_mock,
    db_session_sync,
):
    # Seed a car record
    car = CarModel(
        vin="VINOK123",
        vehicle="Test Vehicle",
        make="Ford",
        model="Focus",
        year=2012,
        transmision="Automatic",
        relevance=RelevanceStatus.ACTIVE,
        auction="Copart",
        auction_name="Copart",
        date=datetime.utcnow(),
    )
    db_session_sync.add(car)
    db_session_sync.commit()

    calls = {"history": 0, "parser": 0}

    class _RespHistory(_RespBase):
        def json(self):
            return {"sales_history": []}

    class _RespParser(_RespBase):
        def json(self):
            return {
                "owners": 2,
                "mileage": 105000,
                "accident_count": 1,
                "jd": 10000,
                "d_max": 12000,
                "manheim": 8000,
                "html_data": "<html>report</html>",
            }

    def _history():
        calls["history"] += 1
        return _RespHistory()

    def _parser():
        calls["parser"] += 1
        return _RespParser()

    http_router_mock(_history, _parser)

    from tasks.task import parse_and_update_car
    out = parse_and_update_car(vin="VINOK123")
    assert out["status"] == "success"

    # Both sales history and main parser should be called once
    assert calls["history"] == 1
    assert calls["parser"] == 1

    # S3 upload was invoked with an HTML report
    assert len(mock_s3_for_task) == 1
    key, html = mock_s3_for_task[0]
    assert key.endswith(".html")
    assert "report" in html

    updated = db_session_sync.query(CarModel).filter_by(vin="VINOK123").first()
    assert updated is not None
    assert updated.relevance == RelevanceStatus.ACTIVE
    assert updated.owners == 2
    assert updated.is_checked is True

    # Average price calculation
    avg = int((10000 + 12000 + 8000) / 3)
    assert updated.avg_market_price == avg

    # Investments = avg / (1 + ROI/100). With ROI=25% we expect avg / 1.25
    expected_invest = avg / 1.25
    assert round(updated.predicted_total_investments, 2) == round(expected_invest, 2)

    # Profit margin amount = avg * profit_margin%. With profit_margin=10% we expect avg * 0.10
    assert round(updated.predicted_profit_margin, 2) == round(avg * 0.10, 2)

    # Auction fee from mock = 5% of investments
    assert round(updated.auction_fee, 2) == round(expected_invest * 0.05, 2)

    # sum_of_investments is a read-only property; code uses (car.sum_of_investments or 0.0)
    # So suggested_bid should equal int(investments - 0)
    assert updated.suggested_bid == int(expected_invest - (updated.sum_of_investments or 0))

    assert updated.predicted_roi == 25.0

    # AutoCheck entry was created with .html URL
    ac = db_session_sync.query(AutoCheckModel).filter_by(car_id=updated.id).first()
    assert ac is not None
    assert ac.screenshot_url.endswith(".html")


def test_http_error_increments_attempts_and_marks_flags(
    patch_task_sessionlocal,
    patch_task_settings,
    mock_roi_and_fees,
    http_router_mock,
    db_session_sync,
):
    # Seed a car and ensure this is the first attempt
    car = CarModel(
        vin="ERRVIN1",
        vehicle="Test Vehicle",
        make="Ford",
        model="Focus",
        year=2012,
        transmision="Automatic",
        relevance=RelevanceStatus.ACTIVE,
        attempts=0,
        auction="Copart",
        auction_name="Copart",
        date=datetime.utcnow(),
    )
    db_session_sync.add(car)
    db_session_sync.commit()

    calls = {"history": 0, "parser": 0}

    class _RespHistory(_RespBase):
        def json(self): return {"sales_history": []}

    class _RespParserBad(_RespBase):
        status_code = 500
        def raise_for_status(self):
            raise Exception("500 internal")

    def _history():
        calls["history"] += 1
        return _RespHistory()

    def _parser():
        calls["parser"] += 1
        return _RespParserBad()

    http_router_mock(_history, _parser)

    from tasks.task import parse_and_update_car
    out = parse_and_update_car(vin="ERRVIN1")
    assert out["status"] == "exception"

    updated = db_session_sync.query(CarModel).filter_by(vin="ERRVIN1").first()
    # Attempts must increment and VIN must be marked incorrect
    assert updated.attempts == 1
    assert updated.has_correct_vin is False
    assert not updated.is_checked


def test_parser_returns_error_field_raises_and_rolls_back(
    patch_task_sessionlocal,
    patch_task_settings,
    mock_roi_and_fees,
    http_router_mock,
    db_session_sync,
):
    # Seed a car
    car = CarModel(
        vin="VINERR2",
        vehicle="Test Vehicle",
        make="Ford",
        model="Focus",
        year=2012,
        transmision="Automatic",
        relevance=RelevanceStatus.ACTIVE,
        auction="Copart",
        auction_name="Copart",
        date=datetime.utcnow(),
    )
    db_session_sync.add(car)
    db_session_sync.commit()

    class _RespHistory(_RespBase):
        def json(self): return {"sales_history": []}

    class _RespParserError(_RespBase):
        def json(self): return {"error": "not found on site anymore"}

    def _history(): return _RespHistory()
    def _parser():  return _RespParserError()

    http_router_mock(_history, _parser)

    from tasks.task import parse_and_update_car
    # Task should raise and rollback
    with pytest.raises(Exception):
        parse_and_update_car(vin="VINERR2")

    updated = db_session_sync.query(CarModel).filter_by(vin="VINERR2").first()
    # No partial state should be persisted on failure
    assert updated.owners is None
    assert not updated.is_checked


def test_sales_history_four_entries_only_verify_invocation_and_effect_flag(
    patch_task_sessionlocal,
    patch_task_settings,
    mock_roi_and_fees,
    http_router_mock,
    db_session_sync,
):
    # 1) Seed car: force first attempt
    car = CarModel(
        vin="VINHIST4",
        vehicle="Test Vehicle",
        make="Ford",
        model="Focus",
        year=2012,
        transmision="Automatic",
        relevance=RelevanceStatus.ACTIVE,
        auction="Copart",
        auction_name="Copart",
        date=datetime.utcnow(),
        attempts=0,
    )
    db_session_sync.add(car)
    db_session_sync.commit()
    db_session_sync.refresh(car)  # ensure attempts == 0 in the Python instance

    calls = {"history": 0, "parser": 0}

    # 2) Valid payload for CarCreateSchema: include vin + 4 sales history entries
    hist_payload = {
        "vin": "VINHIST4",
        "sales_history": [
            {"date": "2024-01-01", "price": 1000, "odometer": 10000, "source": "Copart"},
            {"date": "2023-05-05", "price": 1200, "odometer": 12000, "source": ""},
            {"date": "2022-07-07", "price": 1300, "odometer": 13000, "source": "IAA"},
            {"date": "2021-06-06", "price": 1100, "odometer": 9000},  # without source → code sets "Unknown"
        ],
    }

    class _RespHistory(_RespBase):
        def json(self): return hist_payload

    class _RespParserOK(_RespBase):
        def json(self):
            # Parser returns prices (for average) and owners used in calculations
            return {"jd": 9000, "d_max": 10000, "manheim": 8000, "owners": 1}

    def _history():
        calls["history"] += 1
        return _RespHistory()

    def _parser():
        calls["parser"] += 1
        return _RespParserOK()

    # 3) Wire mocked HTTP router to our fake responses
    http_router_mock(_history, _parser)

    # 4) Run the task
    from tasks.task import parse_and_update_car
    out = parse_and_update_car(vin="VINHIST4")
    assert out["status"] == "success"
    assert calls["history"] == 1  # sales history was actually fetched

    # 5) Verify effect: status and reason
    updated = db_session_sync.query(CarModel).filter_by(vin="VINHIST4").first()
    assert updated.recommendation_status == RecommendationStatus.NOT_RECOMMENDED
    assert "sales at auction in the last 3 years: 4;" in (updated.recommendation_status_reasons or "")
