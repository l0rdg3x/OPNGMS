from fastapi import FastAPI

from app.api.setup import router as setup_router

app = FastAPI(title="OPNGMS", version="0.1.0")

app.include_router(setup_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
