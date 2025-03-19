from fastapi import FastAPI
from api.v1.routers.auth import router as auth_router
from api.v1.routers.user import router as user_router
from core.setup import create_roles
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="My Async FastAPI Project")

@app.on_event("startup")
async def on_startup():
    await create_roles()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1", tags=["Auth"])
app.include_router(user_router, prefix="/api/v1", tags=["User"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
