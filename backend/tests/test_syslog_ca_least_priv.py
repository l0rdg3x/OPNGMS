"""Least-privilege on the syslog CA private key (migration 0040).

The encrypted CA private key lives in the owner-only `syslog_ca_key` table. The app role (`opngms_app`)
must NOT be able to SELECT it via the blanket grant, yet the synchronous device-cert signing path must
still issue certs by reading the key through the SECURITY DEFINER function `opngms_syslog_ca_key()`.
"""
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.core.db import make_engine
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.services.log_forwarding import SyslogCaService


def _app_role_engine():
    """A raw engine that logs in as the real opngms_app role (NOBYPASSRLS, least-priv grants)."""
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    assert app_url.username == APP_ROLE  # fail loudly if the role was not applied
    return make_engine(app_url.render_as_string(hide_password=False))


async def _seed_ca_as_owner(db_engine) -> bytes:
    """Create the CA owner-side (the only path that can write syslog_ca_key). Returns the raw key PEM."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ca = await SyslogCaService(s).ensure_ca()
        await s.commit()
        key_enc = (await s.execute(text("SELECT key_enc FROM syslog_ca_key WHERE id=:i"), {"i": ca.id})).scalar_one()
    return crypto.decrypt_bytes(bytes(key_enc))


async def test_app_role_cannot_select_ca_key(db_engine):
    await _seed_ca_as_owner(db_engine)
    engine = _app_role_engine()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            # The blanket SELECT grant was REVOKEd: reading the key table must fail (insufficient priv).
            with pytest.raises(ProgrammingError):
                await s.execute(text("SELECT key_enc FROM syslog_ca_key"))
    finally:
        await engine.dispose()


async def test_app_role_can_call_key_function(db_engine):
    raw_key = await _seed_ca_as_owner(db_engine)
    engine = _app_role_engine()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            key_enc = (await s.execute(text("SELECT opngms_syslog_ca_key()"))).scalar_one()
        assert key_enc  # non-empty bytea
        assert crypto.decrypt_bytes(bytes(key_enc)) == raw_key
    finally:
        await engine.dispose()


async def test_provision_signs_via_function(db_engine):
    """Under the app role (no key-table access), device_cert must still sign via the function-fetched key."""
    await _seed_ca_as_owner(db_engine)
    engine = _app_role_engine()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            svc = SyslogCaService(s)
            ca = await svc.require_ca()
            cert_pem, key_pem = await svc.device_cert(ca, tenant_id=uuid.uuid4(), device_id=uuid.uuid4())
            assert b"BEGIN CERTIFICATE" in cert_pem
            assert b"BEGIN PRIVATE KEY" in key_pem
    finally:
        await engine.dispose()


async def test_require_ca_raises_when_absent(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        with pytest.raises(RuntimeError, match="syslog CA not initialized"):
            await SyslogCaService(s).require_ca()
