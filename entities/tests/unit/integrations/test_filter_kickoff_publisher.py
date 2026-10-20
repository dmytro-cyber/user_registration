from types import SimpleNamespace
from unittest.mock import Mock

from integrations.vehicle_tasks import CeleryFilterKickoffPublisher


def test_kickoff_publication_keeps_task_queue_kwargs_and_result():
    result = SimpleNamespace(id="kickoff-task-id")
    client = SimpleNamespace(send_task=Mock(return_value=result))
    assert CeleryFilterKickoffPublisher(client).publish(7, 23) is result
    client.send_task.assert_called_once_with(
        "tasks.task.kickoff_parse_for_filter",
        kwargs={"filter_id": 7, "lock_token": "23"},
        queue="car_parsing_queue",
    )
