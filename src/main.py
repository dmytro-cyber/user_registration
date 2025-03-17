from fastapi import FastAPI
from api.v1.routers.auth import router as auth_router

app = FastAPI(title="My Async FastAPI Project")

app.include_router(auth_router, prefix="/api/v1", tags=["Auth"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
