import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.firmware_action import FirmwareAction
from app.services.firmware_action import run_firmware_action


class FakeClient:
    """Scriptable client. `status_seq` feeds firmware_upgrade_status; `reach_fail` makes the next
    N status polls raise ReachabilityError (a reboot) before test_connection succeeds."""
    def __init__(self, *, check=None, status=None, status_seq=None, reach_fail=0):
        self._check = check or {"status": "none"}
        self._status = status or {"status": "none", "updates": "0"}
        self._status_seq = list(status_seq or [{"status": "done"}])
        self._reach_fail = reach_fail
        self.calls = []

    async def firmware_check(self): self.calls.append("check"); return self._check
    async def firmware_status_raw(self): return self._status
    async def firmware_update(self): self.calls.append("update"); return {"status": "ok"}
    async def firmware_upgrade(self): self.calls.append("upgrade"); return {"status": "ok"}
    async def plugin_install(self, name): self.calls.append(f"install:{name}"); return {"status": "ok"}
    async def plugin_remove(self, name): self.calls.append(f"remove:{name}"); return {"status": "ok"}
    async def test_connection(self):
        if self._reach_fail > 0:
            self._reach_fail -= 1
            raise ReachabilityError("rebooting")
        return "26.1.9"
    async def get_device_identity(self):
        from app.connectors.opnsense.identity import DeviceIdentity
        return DeviceIdentity(edition="community", version="26.1.9", series="26.1")
    def set_identity(self, e, v): pass
    async def firmware_upgrade_status(self):
        if self._reach_fail > 0:
            self._reach_fail -= 1
            raise ReachabilityError("rebooting")
        return self._status_seq.pop(0) if len(self._status_seq) > 1 else self._status_seq[0]


async def _action(db_engine, tenant_id, kind, target="", status="scheduled") -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did, aid = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tenant_id})
        await s.execute(text(
            "INSERT INTO firmware_actions (id, tenant_id, device_id, created_by, kind, target, status) "
            "VALUES (:i,:t,:d,:u,:k,:g,:st)"),
            {"i": aid, "t": tenant_id, "d": did, "u": uuid.uuid4(), "k": kind, "g": target, "st": status})
        await s.commit()
    return aid


async def _run(db_engine, aid, client):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        act = await s.get(FirmwareAction, aid)
        st = await run_firmware_action(s, act, client, now=datetime.now(timezone.utc))
        await s.commit()
    async with factory() as s:
        return st, await s.get(FirmwareAction, aid)


async def test_plugin_remove_runs(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "plugin_remove", target="os-acme-client")
    client = FakeClient(status_seq=[{"status": "done"}])
    st, act = await _run(db_engine, aid, client)
    assert st == "done" and act.status == "done"
    assert "remove:os-acme-client" in client.calls


async def test_plugin_install_blocked_when_updates_pending(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "plugin_install", target="os-acme-client")
    client = FakeClient(status={"status": "ok", "updates": "3"})  # updates pending
    st, act = await _run(db_engine, aid, client)
    assert st == "failed" and "up to date" in act.result.get("error", "")
    assert not any(c.startswith("install:") for c in client.calls)  # NO install


async def test_firmware_update_reboot_tolerant(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "firmware_update")
    # status poll raises ReachabilityError twice (reboot) then test_connection comes back, then done
    client = FakeClient(reach_fail=2, status_seq=[{"status": "done"}])
    st, act = await _run(db_engine, aid, client)
    assert st == "done" and "update" in client.calls


async def test_firmware_upgrade_multistep_loop(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "firmware_upgrade")

    # check returns "updates available" for 2 iterations, then "up to date"
    class UpgradeClient(FakeClient):
        def __init__(self):
            super().__init__(status_seq=[{"status": "done"}])
            self._checks = [{"status": "ok"}, {"status": "ok"}, {"status": "none"}]
        async def firmware_status_raw(self):
            return self._checks.pop(0) if len(self._checks) > 1 else self._checks[0]

    client = UpgradeClient()
    st, act = await _run(db_engine, aid, client)
    assert st == "done"
    assert client.calls.count("update") + client.calls.count("upgrade") == 2  # two steps then converged


def _noop():
    async def _a(): return None
    return _a()
