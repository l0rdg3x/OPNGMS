import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.firmware_action import FirmwareAction
from app.services.firmware_action import run_firmware_action
from app.services.runtime_settings import update_runtime_config


class FakeClient:
    """Scriptable client modelling OPNsense's async+serialized firmware runner.
    Each issued action makes `firmware_upgrade_status` report 'running' a couple times then 'done'.
    `reach_fail` makes the next N status/connection polls raise ReachabilityError (a reboot)."""
    def __init__(self, *, status=None, status_seq=None, reach_fail=0, checks=None):
        self._status = status or {"status": "none", "updates": "0"}
        self._checks = list(checks) if checks else None
        self._reach_fail = reach_fail
        self._running = 0
        self.calls = []

    def _begin(self):
        self._running = 2  # the action will be observed running for two polls

    async def firmware_check(self): self.calls.append("check"); self._begin(); return {"status": "ok"}
    async def firmware_status_raw(self):
        if self._checks is not None:
            return self._checks.pop(0) if len(self._checks) > 1 else self._checks[0]
        return self._status
    async def firmware_update(self): self.calls.append("update"); self._begin(); return {"status": "ok"}
    async def firmware_upgrade(self): self.calls.append("upgrade"); self._begin(); return {"status": "ok"}
    async def plugin_install(self, name): self.calls.append(f"install:{name}"); self._begin(); return {"status": "ok"}
    async def plugin_remove(self, name): self.calls.append(f"remove:{name}"); self._begin(); return {"status": "ok"}
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
        if self._running > 0:
            self._running -= 1
            return {"status": "running"}
        return {"status": "done"}


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
    client = FakeClient()
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
    client = FakeClient(reach_fail=2)
    st, act = await _run(db_engine, aid, client)
    assert st == "done" and "update" in client.calls


async def test_firmware_upgrade_multistep_loop(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "firmware_upgrade")

    # check returns "updates available" for 2 iterations, then "up to date"
    client = FakeClient(checks=[{"status": "ok"}, {"status": "ok"}, {"status": "none"}])
    st, act = await _run(db_engine, aid, client)
    assert st == "done"
    assert client.calls.count("update") + client.calls.count("upgrade") == 2  # two steps then converged


async def test_firmware_poll_budget_honors_runtime_override(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    # Shrink the poll budget at runtime; run_firmware_action must pass it into poll_until_done.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await update_runtime_config(s, {"firmware_max_status_polls": 3})
        await s.commit()

    polls = {"n": 0}

    class NeverDone(FakeClient):
        async def firmware_upgrade_status(self):
            polls["n"] += 1
            return {"status": "running"}  # never completes -> budget is the only exit

    aid = await _action(db_engine, ta, "firmware_update")
    st, _act = await _run(db_engine, aid, NeverDone())
    assert st == "failed"
    assert polls["n"] == 3  # bailed at the runtime budget, not the default 360


async def test_poll_until_done_waits_past_stale_done(monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    seq = [{"status": "done"},            # stale "done" from a prior action
           {"status": "running"}, {"status": "running"},
           {"status": "done"}]            # the real completion

    class C:
        async def firmware_upgrade_status(self):
            return seq.pop(0) if len(seq) > 1 else seq[0]

    st = await fa.poll_until_done(C())
    assert st["status"] == "done"
    assert len(seq) == 1  # consumed through the real "done", not the stale one


async def test_poll_until_done_returns_after_grace_when_never_running(monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    calls = {"n": 0}

    class C:
        async def firmware_upgrade_status(self):
            calls["n"] += 1
            return {"status": "done"}  # action never enters "running"

    st = await fa.poll_until_done(C())
    assert st["status"] == "done"
    assert calls["n"] == fa.STARTUP_GRACE_POLLS  # bailed exactly after the grace window


def _noop():
    async def _a(): return None
    return _a()
