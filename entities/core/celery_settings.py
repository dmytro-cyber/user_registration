"""Keep the two legacy brokers distinct even when imported in one process."""

from celery.app.utils import Settings


class ScopedBrokerSettings(Settings):
    @property
    def broker_url(self):
        return self.first("broker_url", "broker_host")

    @property
    def broker_read_url(self):
        return self.get("broker_read_url") or self.broker_url

    @property
    def broker_write_url(self):
        return self.get("broker_write_url") or self.broker_url

    @property
    def result_backend(self):
        return self.first("result_backend", "CELERY_RESULT_BACKEND")


def scope_broker_settings(app):
    # Iterating resolves Celery's pending configuration before wrapping it.
    # All ordinary options remain Celery settings; only broker env lookups differ.
    app.conf = ScopedBrokerSettings(dict(app.conf))
