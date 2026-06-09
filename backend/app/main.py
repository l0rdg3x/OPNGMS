from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.auth import router as auth_router
from app.api.config import router as config_router
from app.api.devices import router as devices_router
from app.api.events import router as events_router
from app.api.me_tenants import router as me_tenants_router
from app.api.memberships import router as memberships_router
from app.api.monitoring import router as monitoring_router
from app.api.setup import router as setup_router
from app.api.tenants import router as tenants_router
from app.api.users import router as users_router

app = FastAPI(title="OPNGMS", version="0.1.0")

app.include_router(setup_router)
app.include_router(auth_router)
app.include_router(tenants_router)
app.include_router(users_router)
app.include_router(memberships_router)
app.include_router(devices_router)
app.include_router(me_tenants_router)
app.include_router(monitoring_router)
app.include_router(events_router)
app.include_router(config_router)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate == "23505":  # unique_violation
        detail = "Conflict: the resource already exists (uniqueness constraint)."
    elif sqlstate == "23503":  # foreign_key_violation
        detail = "Conflict: reference to a nonexistent resource."
    else:
        detail = "Conflict: data integrity violation."
    return JSONResponse(status_code=409, content={"detail": detail})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
