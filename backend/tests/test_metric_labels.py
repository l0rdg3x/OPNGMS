"""friendly_labels: parse assigned interface/gateway names from a device config.xml."""
import gzip
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.services.metric_labels import device_friendly_labels, friendly_labels
from tests.factories import make_tenant

_CONFIG = """<?xml version="1.0"?>
<opnsense>
  <interfaces>
    <wan><if>igb0</if><descr>Uplink Fiber</descr></wan>
    <lan><if>igb1</if><descr>LAN</descr></lan>
    <opt1><if>igb2</if><descr>DMZ</descr></opt1>
    <opt2><if>igb3</if><descr></descr></opt2>
  </interfaces>
  <gateways>
    <gateway_item><name>WAN_GW</name><descr>Primary fiber</descr></gateway_item>
    <gateway_item><name>WAN2_GW</name><descr></descr></gateway_item>
  </gateways>
</opnsense>
"""


def test_friendly_labels_maps_interfaces_and_gateways():
    labels = friendly_labels(_CONFIG)
    assert labels["wan"] == "Uplink Fiber"
    assert labels["lan"] == "LAN"
    assert labels["opt1"] == "DMZ"
    assert labels["WAN_GW"] == "Primary fiber"


def test_friendly_labels_skips_empty_descr():
    labels = friendly_labels(_CONFIG)
    # opt2 + WAN2_GW have no descr -> absent (caller falls back to the raw identifier).
    assert "opt2" not in labels
    assert "WAN2_GW" not in labels


def test_friendly_labels_handles_empty_and_invalid():
    assert friendly_labels("") == {}
    assert friendly_labels("not xml <<<") == {}
    assert friendly_labels("<opnsense></opnsense>") == {}


async def test_device_friendly_labels_from_latest_snapshot(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        did = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                "verify_tls, status, tags) VALUES "
                "(:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": t.id},
        )
        await s.execute(
            text(
                "INSERT INTO config_snapshots (id, tenant_id, device_id, taken_at, canonical_hash, "
                "content_enc, opnsense_version, size_bytes) VALUES "
                "(:id, :t, :d, now(), 'h', :c, '26.1', 1)"
            ),
            {"id": uuid.uuid4(), "t": t.id, "d": did,
             "c": crypto.encrypt_bytes(gzip.compress(_CONFIG.encode("utf-8")))},
        )
        await s.commit()
        tid = t.id
    async with factory() as s:
        labels = await device_friendly_labels(s, tid, did)
    assert labels["opt1"] == "DMZ" and labels["WAN_GW"] == "Primary fiber"


async def test_device_friendly_labels_no_snapshot_degrades(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    async with factory() as s:
        assert await device_friendly_labels(s, tid, uuid.uuid4()) == {}
