from fastapi import FastAPI

from api.v1.routers.apicar import router as apicar_router
from api.v1.routers.parcer import router as parcer_router
from tasks.tasks import fetch_api_data

app = FastAPI(title="My Async FastAPI Project")


@app.on_event("startup")
def on_startup():
    fetch_api_data.delay(base_url="https://api.apicar.store/api/cars/db/all", size=5000)


app.include_router(parcer_router, prefix="/api/v1")
app.include_router(apicar_router, prefix="/api/v1")
