from unittest.mock import MagicMock

from sqlalchemy import select

from models.vehicle import FeeModel


def test_celery_task_keeps_name_result_and_transport_contract(patch_task_sessionlocal, db_session_sync, monkeypatch):
    from tasks import task

    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    response = client.get.return_value
    response.json.return_value = {"fees": {"copart": {"fees": {"gate_fee": {"amount": 95}}}}}
    monkeypatch.setattr(task, "SyncParsingClient", factory)
    assert task.update_car_fees.name == "tasks.task.update_fees"
    assert task.update_car_fees() == {"status": "success"}
    factory.assert_called_once_with(timeout=60)
    client.get.assert_called_once_with("/api/v1/parsers/scrape/fees")
    row = db_session_sync.execute(select(FeeModel)).scalar_one()
    assert (row.auction, row.fee_type, row.amount) == ("copart", "gate_fee", 95)
