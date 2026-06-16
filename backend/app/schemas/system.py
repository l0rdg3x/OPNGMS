from uuid import UUID

from pydantic import BaseModel


class LivePushIn(BaseModel):
    enabled: bool


class LivePushOut(BaseModel):
    enabled: bool


class RuntimeSettingOut(BaseModel):
    key: str
    value: bool | int | float  # effective value (override or default)
    default: bool | int | float  # the env/code default
    kind: str  # "int" | "float" | "bool"
    minimum: float | None = None
    maximum: float | None = None
    group: str


class RetentionImpact(BaseModel):
    """A tenant whose enabled report schedule now over-runs a just-lowered GLOBAL retention default.

    Emitted by ``PUT /api/admin/settings`` only when a superadmin lowers a retention store AND the tenant
    has NO per-tenant override for that store (so it follows the global). The mirror per-tenant warning is
    already shown on the tenant's own Retention card (PR4b); this is the superadmin's immediate feedback.
    """

    tenant_id: UUID
    tenant_name: str
    store: str
    range_days: int
    bound: int


class RuntimeSettingsOut(BaseModel):
    settings: list[RuntimeSettingOut]
    # Non-empty only when this PUT lowered a global retention default and tenants without an override for
    # that store have an enabled schedule whose range now exceeds it; otherwise []. GET always returns [].
    retention_impacts: list[RetentionImpact] = []


class RuntimeSettingsPatch(BaseModel):
    values: dict[str, bool | int | float]


class WebAuthnConfigOut(BaseModel):
    rp_id: str
    rp_name: str
    origin: str
    configured: bool  # rp_id + origin both set -> passkey registration is offered


class WebAuthnConfigIn(BaseModel):
    rp_id: str = ""
    rp_name: str = "OPNGMS"
    origin: str = ""
