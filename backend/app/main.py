from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

import app.services.catalog_kind  # noqa: F401  — registers catalog_setting kind at API-process startup
import app.services.firewall_rule_kind  # noqa: F401  — registers firewall_rule kind at API-process startup
import app.services.ids_kind  # noqa: F401  — registers suricata_ruleset kind at API-process startup
import app.services.monit_kind  # noqa: F401  — registers monit_test kind at startup
import app.services.setting_kind  # noqa: F401  — registers opnsense_setting kind at API-process startup
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.catalog import router as catalog_router
from app.api.config import router as config_router
from app.api.devices import router as devices_router
from app.api.events import router as events_router
from app.api.firewall_rules import router as firewall_rules_router
from app.api.firmware import router as firmware_router
from app.api.groups import router as groups_router
from app.api.ids import router as ids_router
from app.api.log_fleet import router as log_fleet_router
from app.api.log_forwarding import router as log_forwarding_router
from app.api.logs import router as logs_router
from app.api.me_tenants import router as me_tenants_router
from app.api.memberships import router as memberships_router
from app.api.mfa import router as mfa_router
from app.api.monit import router as monit_router
from app.api.monitoring import router as monitoring_router
from app.api.profiles import router as profiles_router
from app.api.report_schedules import router as report_schedules_router
from app.api.reports import router as reports_router
from app.api.settings import router as settings_router
from app.api.setup import router as setup_router
from app.api.smtp import router as smtp_router
from app.api.system import router as system_router
from app.api.templates import router as templates_router
from app.api.tenants import router as tenants_router
from app.api.users import router as users_router
from app.core.config import assert_secure_secrets, get_settings
from app.core.security import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed at startup if any secret is still an .env.example placeholder (weak default creds).
    assert_secure_secrets(get_settings())
    yield


app = FastAPI(title="OPNGMS", version="0.1.0", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)
_origins = [o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(setup_router)
app.include_router(auth_router)
app.include_router(mfa_router)
app.include_router(tenants_router)
app.include_router(users_router)
app.include_router(groups_router)
app.include_router(memberships_router)
app.include_router(devices_router)
app.include_router(me_tenants_router)
app.include_router(monitoring_router)
app.include_router(events_router)
app.include_router(config_router)
app.include_router(catalog_router)
app.include_router(firmware_router)
app.include_router(reports_router)
app.include_router(report_schedules_router)
app.include_router(templates_router)
app.include_router(profiles_router)
app.include_router(settings_router)
app.include_router(ids_router)
app.include_router(firewall_rules_router)
app.include_router(monit_router)
app.include_router(smtp_router)
app.include_router(log_forwarding_router)
app.include_router(logs_router)
app.include_router(log_fleet_router)
app.include_router(system_router)
app.include_router(audit_router)


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
