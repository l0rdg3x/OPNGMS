import pytest
from fastapi import HTTPException

from app.core import deps
from app.models.session import Session


class _Svc:
    def __init__(self, user):
        self._u = user

    async def get_user_for_session(self, sess):
        return self._u


async def test_get_current_user_rejects_mfa_setup():
    sess = Session(kind="mfa_setup")
    with pytest.raises(HTTPException) as ei:
        await deps.get_current_user(sess=sess, session=None)
    assert ei.value.status_code == 403
    assert ei.value.detail == "mfa_setup_required"


async def test_get_current_user_rejects_mfa_pending():
    sess = Session(kind="mfa_pending")
    with pytest.raises(HTTPException) as ei:
        await deps.get_current_user(sess=sess, session=None)
    assert ei.value.status_code == 403
    assert ei.value.detail == "mfa_required"


async def test_get_current_user_allows_full(monkeypatch):
    sess = Session(kind="full")
    user = object()
    monkeypatch.setattr(deps, "AuthService", lambda s: _Svc(user))
    out = await deps.get_current_user(sess=sess, session=None)
    assert out is user


async def test_get_enrollment_ctx_allows_setup(monkeypatch):
    sess = Session(kind="mfa_setup")
    user = object()
    monkeypatch.setattr(deps, "AuthService", lambda s: _Svc(user))
    out_user, out_sess = await deps.get_enrollment_ctx(sess=sess, session=None)
    assert out_user is user and out_sess is sess


async def test_get_enrollment_ctx_rejects_pending():
    sess = Session(kind="mfa_pending")
    with pytest.raises(HTTPException) as ei:
        await deps.get_enrollment_ctx(sess=sess, session=None)
    assert ei.value.status_code == 401
