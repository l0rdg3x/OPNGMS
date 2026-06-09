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
        (False, READ_ONLY, Action.AUDIT_VIEW, True),
    ],
)
def test_permission_matrix(is_superadmin, role, action, expected):
    assert can(is_superadmin=is_superadmin, role=role, action=action) is expected
