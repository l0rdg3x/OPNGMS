from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.memberships import router as memberships_router
from app.api.setup import router as setup_router
from app.api.tenants import router as tenants_router
from app.api.users import router as users_router

app = FastAPI(title="OPNGMS", version="0.1.0")

app.include_router(setup_router)
app.include_router(auth_router)
app.include_router(tenants_router)
app.include_router(users_router)
app.include_router(memberships_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
