from unittest.mock import Mock

from integrations.vehicle_tasks import CeleryFilterUpdateKickoffPublisher


def test_update_keeps_direct_task_numeric_filter_lock_token_and_queue():
    client = Mock()
    assert CeleryFilterUpdateKickoffPublisher(client).publish(27) is None
    client.send_task.assert_called_once_with(
        "tasks.task.kickoff_parse_for_filter",
        kwargs={"filter_id": 27, "lock_token": 27},
        queue="car_parsing_queue",
    )
