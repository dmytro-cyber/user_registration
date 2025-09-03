import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.routers.admin import router as admin_router
from api.v1.routers.analytic import router as analytics_router
from api.v1.routers.auth import router as auth_router
from api.v1.routers.bidding_hub import router as bidding_hub_router
from api.v1.routers.inventory import router as inventory_router
from api.v1.routers.user import router as user_router
from api.v1.routers.vehicle import router as vehicle_router
from core.celery_config import app as celery_app
from core.setup import create_roles, import_us_zips_from_csv, match_and_update_locations
# from tasks.task import update_car_fees

app = FastAPI(
    title="Cars&Beyond API",
    description="Cars&Beyond API for managing vehicles, users, and bidding",
)


@app.on_event("startup")
async def on_startup():
    await create_roles()
    celery_app.send_task(
        "tasks.task.update_fees",
        queue="car_parsing_queue",
    )
    await import_us_zips_from_csv()

# @app.on_event("startup")
# async def on_startup():


# @app.on_event("startup")
# async def on_startup():

#     await match_and_update_locations()


app.add_middleware(
    CORSMiddleware,
    # allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origins=[
        "https://localhost:5173",
        "https://127.0.0.1:5173",
        "https://cars-beyond-git-dev-mykola-bals-projects.vercel.app",
        "https://cars-beyond.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["set-cookie"],
)

app.include_router(auth_router, prefix="/api/v1", tags=["Auth"])
app.include_router(user_router, prefix="/api/v1", tags=["User"])
app.include_router(vehicle_router, prefix="/api/v1", tags=["Vehicle"])
app.include_router(admin_router, prefix="/api/v1", tags=["Admin"])
app.include_router(bidding_hub_router, prefix="/api/v1", tags=["Bidding Hub"])
app.include_router(inventory_router, prefix="/api/v1", tags=["Inventory"])
app.include_router(analytics_router, prefix="/api/v1", tags=["Analytics"])

def configure_logging():
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


    for name in ("sqlalchemy", "sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool", "sqlalchemy.dialects"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.propagate = False
        lg.handlers.clear()

configure_logging()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
