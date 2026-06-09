import enum

# Ruoli per-tenant (assegnati via Membership). 'superadmin' è un flag a livello utente.
TENANT_ADMIN = "tenant_admin"
OPERATOR = "operator"
READ_ONLY = "read_only"
TENANT_ROLES = {TENANT_ADMIN, OPERATOR, READ_ONLY}


class Action(str, enum.Enum):
    # org-level (solo superadmin)
    TENANT_MANAGE = "tenant.manage"
    USER_MANAGE = "user.manage"
    # tenant-level
    MEMBERSHIP_MANAGE = "membership.manage"
    DEVICE_VIEW = "device.view"
    DEVICE_WRITE = "device.write"
    AUDIT_VIEW = "audit.view"


# Azioni org-level: consentite SOLO al superadmin (nessun ruolo per-tenant le concede).
_ORG_ACTIONS = {Action.TENANT_MANAGE, Action.USER_MANAGE}

# Azioni tenant-level -> ruoli che le concedono (oltre al superadmin, sempre ammesso).
_TENANT_MATRIX: dict[Action, set[str]] = {
    Action.MEMBERSHIP_MANAGE: {TENANT_ADMIN},
    Action.DEVICE_VIEW: {TENANT_ADMIN, OPERATOR, READ_ONLY},
    Action.DEVICE_WRITE: {TENANT_ADMIN, OPERATOR},
    Action.AUDIT_VIEW: {TENANT_ADMIN, OPERATOR, READ_ONLY},
}


def can(*, is_superadmin: bool, role: str | None, action: Action) -> bool:
    if is_superadmin:
        return True
    if action in _ORG_ACTIONS:
        return False
    return role in _TENANT_MATRIX.get(action, set())
