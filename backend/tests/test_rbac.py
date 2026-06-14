from app.core.rbac import TENANT_ADMIN, Action, can


def test_audit_view_is_org_level_superadmin_only():
    # Superadmin (no tenant role) can view the global audit log.
    assert can(is_superadmin=True, role=None, action=Action.AUDIT_VIEW) is True
    # A tenant_admin must NOT reach the global, cross-tenant audit log.
    assert can(is_superadmin=False, role=TENANT_ADMIN, action=Action.AUDIT_VIEW) is False
