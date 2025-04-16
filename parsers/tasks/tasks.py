from celery import Celery
from celery.schedules import crontab
import httpx

# Celery configuration
app = Celery(
    "tasks",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/0",
)

# Celery beat configuration
app.conf.beat_schedule = {
    "fetch-api-data-every-minute": {
        "task": "tasks.tasks.fetch_api_data",
        "schedule": crontab(minute="*/1"),
    },
}

app.conf.timezone = "UTC"

@app.task
def fetch_api_data():
    url = "https://api.example.com/data"
    print("Fetching data from API...")
    # try:
    #     response = httpx.get(url, timeout=10)
    #     response.raise_for_status()
    #     data = response.json()
    #     print(f"API data: {data}")
    #     return data
    # except httpx.HTTPError as e:
    #     print(f"Failed to fetch API data: {e}")
    #     return None