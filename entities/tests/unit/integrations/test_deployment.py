from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("name", ["compose.yaml", "compose.prod.yaml"])
def test_workers_use_one_codebase_and_separate_queues(name):
    root = Path(__file__).parents[4]
    services = yaml.safe_load((root / name).read_text())["services"]
    assert "parsers" not in services
    assert "8001:8000" in services["entities"]["ports"]
    for key, queue in (("celery_worker", "celery"), ("scraping_worker", "scraping")):
        worker = services[key]
        assert worker["build"]["dockerfile"] == "entities/Dockerfile"
        command = worker["command"]
        assert command[command.index("-P") + 1] == "solo"
        assert command[command.index("-Q") + 1] == queue
    assert services["scraping_worker"]["build"]["target"] == "scraping"
    assert services["celery_beat"]["command"][2] == "core.integration_celery.app"
    assert services["celery_beat_entities"]["command"][2] == "core.celery_config.app"
    assert "--schedule=/var/lib/celery/integrations-schedule" in services["celery_beat"]["command"]
    assert "--schedule=/var/lib/celery/entities-schedule" in services["celery_beat_entities"]["command"]
    for name in ("celery_beat", "celery_beat_entities"):
        assert "celery_beat_state:/var/lib/celery" in services[name]["volumes"]
