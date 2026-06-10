"""Tests that APP_ROLE_PASSWORD in app.core.db_roles honours the APP_ROLE_PASSWORD env var.

This test is self-contained: it uses importlib.reload to force the module to re-read os.getenv,
then restores the module to its default state at teardown so other tests/imports that rely on
the default password ("opngms_app") are unaffected.
"""

import importlib

import app.core.db_roles as db_roles_module


def test_app_role_password_default():
    """Without the env var, the password falls back to the dev default."""
    # The module was loaded without APP_ROLE_PASSWORD set (typical test environment).
    assert db_roles_module.APP_ROLE_PASSWORD == "opngms_app"


def test_app_role_password_reads_env(monkeypatch):
    """When APP_ROLE_PASSWORD is set in the environment, the module constant reflects it."""
    monkeypatch.setenv("APP_ROLE_PASSWORD", "s3cret")
    try:
        importlib.reload(db_roles_module)
        assert db_roles_module.APP_ROLE_PASSWORD == "s3cret"
    finally:
        # Always restore the module to its default state regardless of test outcome.
        monkeypatch.delenv("APP_ROLE_PASSWORD", raising=False)
        importlib.reload(db_roles_module)
        assert db_roles_module.APP_ROLE_PASSWORD == "opngms_app"
