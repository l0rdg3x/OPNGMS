import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.config_profile import ConfigProfile
from app.services.profiles import materialize_profile
from app.services.templates import InvalidTemplateError

# Two valid firewall_alias bodies the template engine accepts.
_BODY_A = {"name": "web_a", "type": "host", "content": ["1.2.3.4"]}
_BODY_B = {"name": "web_b", "type": "network", "content": ["10.0.0.0/24"]}


async def _insert_tenant(db_engine, slug):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status) "
                "VALUES (:id, :n, :s, 'active')"
            ),
            {"id": tid, "n": slug.upper(), "s": slug},
        )
        await s.commit()
    return tid


async def _insert_device(db_engine, tenant_id, name="fw1"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name},
        )
        await s.commit()
    return did


async def _seed_template(db_engine, *, kind="firewall_alias", name, body):
    """Insert a global config_templates row via the owner engine (global, no tenant context)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tpl_id = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO config_templates "
                "(id, kind, name, description, body, version, created_by) "
                "VALUES (:id, :k, :n, '', CAST(:b AS jsonb), 1, :u)"
            ),
            {"id": tpl_id, "k": kind, "n": name, "b": json.dumps(body), "u": uuid.uuid4()},
        )
        await s.commit()
    return tpl_id


async def _seed_profile(db_engine, *, name, member_template_ids):
    """Insert a config_profiles row + ordered config_profile_members via the owner engine (global)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    pid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO config_profiles "
                "(id, name, description, version, created_by) "
                "VALUES (:id, :n, '', 1, :u)"
            ),
            {"id": pid, "n": name, "u": uuid.uuid4()},
        )
        for position, template_id in enumerate(member_template_ids):
            await s.execute(
                text(
                    "INSERT INTO config_profile_members "
                    "(id, profile_id, template_id, position) "
                    "VALUES (:id, :p, :t, :pos)"
                ),
                {"id": uuid.uuid4(), "p": pid, "t": template_id, "pos": position},
            )
        await s.commit()
    return pid


async def _seed_override(db_engine, tenant_id, template_id, body_patch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    oid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO template_overrides "
                "(id, template_id, tenant_id, body_patch) "
                "VALUES (:id, :tpl, :t, CAST(:p AS jsonb))"
            ),
            {"id": oid, "tpl": template_id, "t": tenant_id, "p": json.dumps(body_patch)},
        )
        await s.commit()
    return oid


async def _load_profile(db_engine, profile_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        return await s.get(ConfigProfile, profile_id)


async def _count_changes_for_device(db_engine, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        return (
            await s.execute(
                text("SELECT count(*) FROM config_changes WHERE device_id = :d"),
                {"d": device_id},
            )
        ).scalar_one()


async def test_materialize_profile_fans_out_in_order(db_engine, two_tenants):
    """Two ordered members -> two config_changes in order, each tagged with both
    source_template_id and source_profile_id, kind 'alias'."""
    tid = await _insert_tenant(db_engine, "acme")
    did = await _insert_device(db_engine, tid)
    tpl_a = await _seed_template(db_engine, name="alias-a", body=_BODY_A)
    tpl_b = await _seed_template(db_engine, name="alias-b", body=_BODY_B)
    pid = await _seed_profile(db_engine, name="bundle", member_template_ids=[tpl_a, tpl_b])
    profile = await _load_profile(db_engine, pid)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        changes = await materialize_profile(
            s, tenant_id=tid, device_id=did, created_by=uuid.uuid4(), profile=profile
        )
        await s.commit()

    # Two changes, in member order.
    assert len(changes) == 2
    assert changes[0].source_template_id == tpl_a
    assert changes[1].source_template_id == tpl_b
    # Each is tagged with the profile and is an alias change.
    for ch in changes:
        assert ch.source_profile_id == pid
        assert ch.kind == "alias"
    # Targets follow the (pinned) effective alias names, in order.
    assert changes[0].target == _BODY_A["name"]
    assert changes[1].target == _BODY_B["name"]

    # Persisted: exactly two config_changes for this device.
    assert await _count_changes_for_device(db_engine, did) == 2


async def test_materialize_profile_invalid_member_creates_zero_changes(db_engine, two_tenants):
    """If ANY member's effective body is invalid, materialize raises and creates NOTHING."""
    tid = await _insert_tenant(db_engine, "beta")
    did = await _insert_device(db_engine, tid)
    tpl_a = await _seed_template(db_engine, name="ok-a", body=_BODY_A)
    tpl_b = await _seed_template(db_engine, name="ok-b", body=_BODY_B)
    pid = await _seed_profile(db_engine, name="bad-bundle", member_template_ids=[tpl_a, tpl_b])
    # A tenant override that empties the SECOND member's content -> invalid effective body.
    await _seed_override(db_engine, tid, tpl_b, {"content": []})
    profile = await _load_profile(db_engine, pid)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        with pytest.raises(InvalidTemplateError):
            await materialize_profile(
                s, tenant_id=tid, device_id=did, created_by=uuid.uuid4(), profile=profile
            )
        await s.rollback()

    # Validate-all-before-create: not even the valid first member produced a change.
    assert await _count_changes_for_device(db_engine, did) == 0


async def test_materialize_profile_empty_profile_raises(db_engine, two_tenants):
    """A profile with no member templates is invalid and creates nothing."""
    tid = await _insert_tenant(db_engine, "gamma")
    did = await _insert_device(db_engine, tid)
    pid = await _seed_profile(db_engine, name="empty", member_template_ids=[])
    profile = await _load_profile(db_engine, pid)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        with pytest.raises(InvalidTemplateError):
            await materialize_profile(
                s, tenant_id=tid, device_id=did, created_by=uuid.uuid4(), profile=profile
            )
        await s.rollback()

    assert await _count_changes_for_device(db_engine, did) == 0
