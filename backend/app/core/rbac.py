import enum

# Per-tenant roles (assigned via Membership). 'superadmin' is a user-level flag.
TENANT_ADMIN = "tenant_admin"
OPERATOR = "operator"
READ_ONLY = "read_only"
TENANT_ROLES = {TENANT_ADMIN, OPERATOR, READ_ONLY}


class Action(str, enum.Enum):
    # org-level (superadmin only)
    TENANT_MANAGE = "tenant.manage"
    USER_MANAGE = "user.manage"
    TEMPLATE_MANAGE = "template.manage"
    GROUP_MANAGE = "group.manage"
    LOG_FLEET_VIEW = "log_fleet.view"
    SYSTEM_MANAGE = "system.manage"
    AUDIT_VIEW = "audit.view"  # global cross-tenant audit log -> superadmin only
    # tenant-level
    MEMBERSHIP_MANAGE = "membership.manage"
    DEVICE_VIEW = "device.view"
    DEVICE_WRITE = "device.write"
    CONFIG_PUSH = "config.push"
    REPORT_GENERATE = "report.generate"
    REPORT_CONFIG = "report.config"
    LOG_VIEW = "log.view"


# Org-level actions: allowed ONLY to the superadmin (no per-tenant role grants them).
_ORG_ACTIONS = {
    Action.TENANT_MANAGE,
    Action.USER_MANAGE,
    Action.TEMPLATE_MANAGE,
    Action.GROUP_MANAGE,
    Action.LOG_FLEET_VIEW,
    Action.SYSTEM_MANAGE,
    Action.AUDIT_VIEW,
}

# Tenant-role precedence (highest wins) when a user reaches a tenant via several paths
# (direct membership + one or more group grants).
ROLE_RANK: dict[str, int] = {READ_ONLY: 1, OPERATOR: 2, TENANT_ADMIN: 3}


def highest_role(roles) -> str | None:
    """Return the most-privileged tenant role among `roles` (ignoring None/unknown), else None."""
    best: str | None = None
    best_rank = 0
    for role in roles:
        rank = ROLE_RANK.get(role or "", 0)
        if rank > best_rank:
            best, best_rank = role, rank
    return best

# Tenant-level actions -> roles that grant them (besides the superadmin, always allowed).
_TENANT_MATRIX: dict[Action, set[str]] = {
    Action.MEMBERSHIP_MANAGE: {TENANT_ADMIN},
    Action.DEVICE_VIEW: {TENANT_ADMIN, OPERATOR, READ_ONLY},
    Action.DEVICE_WRITE: {TENANT_ADMIN, OPERATOR},
    Action.CONFIG_PUSH: {TENANT_ADMIN, OPERATOR},
    Action.REPORT_GENERATE: {TENANT_ADMIN, OPERATOR},
    Action.REPORT_CONFIG: {TENANT_ADMIN},
    Action.LOG_VIEW: {TENANT_ADMIN, OPERATOR},
}


def can(*, is_superadmin: bool, role: str | None, action: Action) -> bool:
    if is_superadmin:
        return True
    if action in _ORG_ACTIONS:
        return False
    return role in _TENANT_MATRIX.get(action, set())
