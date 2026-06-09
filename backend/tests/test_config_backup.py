import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.core import crypto
from app.models.device import Device
from app.services.config_backup import backup_config

XML1 = "<opnsense><revision><time>1</time></revision><system><hostname>fw1</hostname></system></opnsense>"
XML1B = "<opnsense><revision><time>2</time></revision><system><hostname>fw1</hostname></system></opnsense>"  # re-save only
XML2 = "<opnsense><revision><time>3</time></revision><system><hostname>fw2</hostname></system></opnsense>"  # changed


class FakeClient:
    def __init__(self, xml, fail=False):
        self._xml = xml
        self._fail = fail

    async def get_config_backup(self):
        if self._fail:
            raise ReachabilityError("boom")
        return self._xml


async def _device(db_engine, tenant_id) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags, firmware_version) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}', '24.7')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.commit()
    return did


async def test_first_backup_inserts_snapshot(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        created = await backup_config(s, device, FakeClient(XML1))
        await s.commit()
    assert created is True
    async with factory() as s:
        row = (await s.execute(
            text("SELECT content_enc, opnsense_version FROM config_snapshots WHERE device_id=:d"),
            {"d": did},
        )).one()
    # content is encrypted (not the raw XML), version tagged
    assert bytes(row.content_enc) != XML1.encode()
    assert row.opnsense_version == "24.7"


async def test_resave_does_not_create_new_version(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for xml in (XML1, XML1B):  # only <revision> differs
        async with factory() as s:
            device = await s.get(Device, did)
            await backup_config(s, device, FakeClient(xml))
            await s.commit()
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 1  # dedup-on-change


async def test_real_change_creates_new_version(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for xml in (XML1, XML2):
        async with factory() as s:
            device = await s.get(Device, did)
            await backup_config(s, device, FakeClient(xml))
            await s.commit()
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 2


async def test_connector_error_skips(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        created = await backup_config(s, device, FakeClient("", fail=True))
        await s.commit()
    assert created is False
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 0


async def test_hostile_xml_is_skipped(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;&a;">]>'
        "<opnsense><x>&b;</x></opnsense>"
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        created = await backup_config(s, device, FakeClient(bomb))  # defusedxml refuses -> skip
        await s.commit()
    assert created is False
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 0
