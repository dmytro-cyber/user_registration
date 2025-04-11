from fastapi import FastAPI
from api.v1.routers.auth import router as auth_router
from api.v1.routers.user import router as user_router
from api.v1.routers.vehicle import router as vehicle_router
from core.setup import create_roles, import_cars_from_csv
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="My Async FastAPI Project")


@app.on_event("startup")
async def on_startup():
    await create_roles()
    await import_cars_from_csv("../car_data.csv")


app.add_middleware(
    CORSMiddleware,
    # allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origins=["https://localhost:5173", "https://127.0.0.1:5173", "https://cars-beyond-git-dev-mykola-bals-projects.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["set-cookie"],
)

app.include_router(auth_router, prefix="/api/v1", tags=["Auth"])
app.include_router(user_router, prefix="/api/v1", tags=["User"])
app.include_router(vehicle_router, prefix="/api/v1", tags=["Vehicle"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
