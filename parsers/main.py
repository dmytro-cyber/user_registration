from fastapi import FastAPI

from api.v1.routers.apicar import router as apicar_router
from api.v1.routers.parcer import router as parcer_router

app = FastAPI(title="My Async FastAPI Project")


app.include_router(parcer_router, prefix="/api/v1")
app.include_router(apicar_router, prefix="/api/v1")
