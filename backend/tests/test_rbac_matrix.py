import pytest

from app.core.rbac import (
    OPERATOR,
    READ_ONLY,
    TENANT_ADMIN,
    Action,
    can,
)


@pytest.mark.parametrize(
    "is_superadmin,role,action,expected",
    [
        (True, None, Action.TENANT_MANAGE, True),
        (False, TENANT_ADMIN, Action.TENANT_MANAGE, False),
        (False, TENANT_ADMIN, Action.USER_MANAGE, False),
        (True, None, Action.USER_MANAGE, True),
        (False, TENANT_ADMIN, Action.MEMBERSHIP_MANAGE, True),
        (False, OPERATOR, Action.MEMBERSHIP_MANAGE, False),
        (True, None, Action.MEMBERSHIP_MANAGE, True),
        (False, READ_ONLY, Action.DEVICE_VIEW, True),
        (False, OPERATOR, Action.DEVICE_WRITE, True),
        (False, READ_ONLY, Action.DEVICE_WRITE, False),
        # AUDIT_VIEW is org-level (global cross-tenant ledger) -> superadmin only, no tenant role.
        (True, None, Action.AUDIT_VIEW, True),
        (False, READ_ONLY, Action.AUDIT_VIEW, False),
        # Hardening: no tenant role can perform org-level actions (anti-escalation)
        (False, OPERATOR, Action.TENANT_MANAGE, False),
        (False, READ_ONLY, Action.TENANT_MANAGE, False),
        (False, OPERATOR, Action.USER_MANAGE, False),
        (False, READ_ONLY, Action.USER_MANAGE, False),
        (False, READ_ONLY, Action.MEMBERSHIP_MANAGE, False),
        # Positive-path tenant_admin sulle azioni tenant
        (False, TENANT_ADMIN, Action.DEVICE_VIEW, True),
        (False, TENANT_ADMIN, Action.DEVICE_WRITE, True),
        (False, TENANT_ADMIN, Action.AUDIT_VIEW, False),
        # CONFIG_PUSH: granted to tenant_admin + operator + superadmin, denied to read_only
        (False, TENANT_ADMIN, Action.CONFIG_PUSH, True),
        (False, OPERATOR, Action.CONFIG_PUSH, True),
        (True, None, Action.CONFIG_PUSH, True),
        (False, READ_ONLY, Action.CONFIG_PUSH, False),
    ],
)
def test_permission_matrix(is_superadmin, role, action, expected):
    assert can(is_superadmin=is_superadmin, role=role, action=action) is expected


def test_report_generate_grants():
    assert can(is_superadmin=False, role=TENANT_ADMIN, action=Action.REPORT_GENERATE)
    assert can(is_superadmin=False, role=OPERATOR, action=Action.REPORT_GENERATE)
    assert not can(is_superadmin=False, role=READ_ONLY, action=Action.REPORT_GENERATE)
    assert can(is_superadmin=True, role=None, action=Action.REPORT_GENERATE)


def test_report_config_grants():
    # REPORT_CONFIG: granted to tenant_admin only; operator + read_only denied; superadmin allowed
    assert can(is_superadmin=False, role=TENANT_ADMIN, action=Action.REPORT_CONFIG)
    assert not can(is_superadmin=False, role=OPERATOR, action=Action.REPORT_CONFIG)
    assert not can(is_superadmin=False, role=READ_ONLY, action=Action.REPORT_CONFIG)
    assert can(is_superadmin=True, role=None, action=Action.REPORT_CONFIG)
