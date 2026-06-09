from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.setup import router as setup_router

app = FastAPI(title="OPNGMS", version="0.1.0")

app.include_router(setup_router)
app.include_router(auth_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
