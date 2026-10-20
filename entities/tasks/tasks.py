"""Compatibility import for existing Celery commands and registered task names."""

from core.integration_celery import app as app
from tasks.ingestion import delete_vehicle as delete_vehicle
from tasks.ingestion import fetch_api_data as fetch_api_data
