"""Integration tests for features.ui_manager (zero heavy deps)."""

from __future__ import annotations

import asyncio
import http.client
import json

import pytest

from features.common.errors import AuthError, BackendUnavailableError, ValidationError
from features.common.http import HttpServer
from features.inference_engine import InferenceEngine, MockBackend
from features.ui_manager import (
    BakeOff,
    DeviceFlow,
    HeadlessUI,
    NativeAuth,
    StateStore,
    build_app,
    hash_password,
    verify_password,
)
from features.ui_manager.server import make_auth_server


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------


def test_hash_password_roundtrip():
    h, s = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h, s)
    assert not verify_password("wrong", h, s)


def test_hash_password_salts_differ():
    h1, s1 = hash_password("same")
    h2, s2 = hash_password("same")
    assert s1 != s2
    assert h1 != h2
    assert verify_password("same", h2, s2)


def test_verify_password_bad_salt():
    h, _ = hash_password("x")
    assert not verify_password("x", h, "zz")  # invalid hex salt -> False


# --------------------------------------------------------------------------
# NativeAuth
# --------------------------------------------------------------------------


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def auth(clock):
    a = NativeAuth(session_ttl=100, max_attempts=3, lockout_seconds=50, clock=clock)
    a.add_user("alice", "wonderland")
    return a


def test_auth_register_login_whoami(auth, clock):
    token = auth.login("alice", "wonderland")
    assert auth.whoami(token) == "alice"
    assert auth.active_sessions() == 1
    auth.logout(token)
    assert auth.whoami(token) is None
    assert auth.active_sessions() == 0


def test_auth_bad_password(auth):
    with pytest.raises(AuthError):
        auth.login("alice", "nope")
    with pytest.raises(AuthError):
        auth.login("mallory", "wonderland")


def test_auth_lockout_after_max_attempts(auth, clock):
    for _ in range(3):
        with pytest.raises(AuthError):
            auth.login("alice", "bad")
    # locked even with the right password
    with pytest.raises(AuthError) as ei:
        auth.login("alice", "wonderland")
    assert "locked" in ei.value.message
    clock.advance(51)
    assert auth.login("alice", "wonderland")  # unlocked after TTL


def test_auth_session_expiry(auth, clock):
    token = auth.login("alice", "wonderland")
    assert auth.whoami(token) == "alice"
    clock.advance(101)
    assert auth.whoami(token) is None


def test_auth_users_file_persistence(tmp_path, clock):
    f = str(tmp_path / "users.ndjson")
    a1 = NativeAuth(users_file=f, clock=clock)
    a1.add_user("bob", "builder")
    a2 = NativeAuth(users_file=f, clock=clock)
    assert a2.user_exists("bob")
    assert a2.login("bob", "builder")


def test_auth_register_validation(auth):
    with pytest.raises(ValidationError):
        auth.add_user("", "x")
    with pytest.raises(ValidationError):
        auth.add_user("alice", "again")


# --------------------------------------------------------------------------
# Device flow (OAuth 8628-shaped)
# --------------------------------------------------------------------------


def test_device_flow_full_lifecycle(auth, clock):
    devices = DeviceFlow(auth, clock=clock)
    device_code, user_code = devices.create_code("alice")
    assert devices.poll(device_code) == {"status": "pending"}
    devices.approve(user_code)
    result = devices.poll(device_code)
    assert result["status"] == "approved"
    assert auth.whoami(result["token"]) == "alice"
    # code consumed
    with pytest.raises(ValidationError):
        devices.poll(device_code)


def test_device_flow_case_insensitive_code(auth, clock):
    devices = DeviceFlow(auth, clock=clock)
    _, user_code = devices.create_code("alice")
    devices.approve(user_code.lower())
    device_code = [k for k in devices._pending][0]
    assert devices.poll(device_code)["status"] == "approved"


def test_device_flow_expiry(auth, clock):
    devices = DeviceFlow(auth, clock=clock, ttl_seconds=60)
    device_code, _ = devices.create_code("alice")
    clock.advance(61)
    assert devices.poll(device_code) == {"status": "expired"}


def test_device_flow_unknown_codes(auth, clock):
    devices = DeviceFlow(auth, clock=clock)
    with pytest.raises(ValidationError):
        devices.approve("XXXXXXXX")
    with pytest.raises(ValidationError):
        devices.poll("nope")
    with pytest.raises(ValidationError):
        devices.create_code("ghost")


# --------------------------------------------------------------------------
# State store
# --------------------------------------------------------------------------


def test_state_save_load_roundtrip(tmp_path):
    store = StateStore(str(tmp_path))
    store.save("alice", {"model": "mock-1b", "conv": [1, 2]})
    assert store.load("alice") == {"model": "mock-1b", "conv": [1, 2]}


def test_state_journal_replay(tmp_path):
    store = StateStore(str(tmp_path))
    store.save("alice", {"v": 1})
    store.save("alice", {"v": 2})
    journal = store.replay("alice")
    assert [e["state"]["v"] for e in journal] == [1, 2]
    assert store.load("alice") == {"v": 2}


def test_state_per_user_isolation(tmp_path):
    store = StateStore(str(tmp_path))
    store.save("alice", {"who": "a"})
    store.save("bob", {"who": "b"})
    assert store.load("alice") == {"who": "a"}
    assert store.load("bob") == {"who": "b"}
    assert store.users() == ["alice", "bob"]
    assert store.load("carol") is None


def test_state_forget(tmp_path):
    store = StateStore(str(tmp_path))
    store.save("alice", {"x": 1})
    store.forget("alice")
    assert store.load("alice") is None


# --------------------------------------------------------------------------
# Bake-off
# --------------------------------------------------------------------------


@pytest.fixture
def engine():
    e = InferenceEngine()
    e.register(MockBackend("model-a"))
    e.register(MockBackend("model-b"))
    e.register(MockBackend("model-c", sleep_per_token=0.001))
    return e


async def test_bakeoff_run_all_models(engine):
    entries = await BakeOff(engine, ["model-a", "model-b", "model-c"]).run("compare me")
    assert [e.model for e in entries] == ["model-a", "model-b", "model-c"]
    assert all(e.ok for e in entries)
    assert all(e.tokens > 0 for e in entries)
    assert all(e.text for e in entries)
    # each model streams independently (deterministic but distinct)
    texts = [e.text for e in entries]
    assert len(set(texts)) >= 2


async def test_bakeoff_reports_failures(engine):
    entries = await BakeOff(engine, ["model-a", "ghost-model"]).run("x")
    by_model = {e.model: e for e in entries}
    assert by_model["model-a"].ok
    assert not by_model["ghost-model"].ok
    assert "not registered" in (by_model["ghost-model"].error or "")


async def test_bakeoff_meets_target_flag(engine):
    entries = await BakeOff(engine, ["model-a"]).run("fast")
    assert entries[0].meets_target is True  # mock >> 80 t/s
    assert entries[0].tokens_per_sec >= 80


async def test_bakeoff_astream_live_events(engine):
    events = []
    async for ev in BakeOff(engine, ["model-a", "model-b"]).astream("stream bake"):
        events.append(ev)
    types = [e["type"] for e in events]
    assert types[0] == "bakeoff_start"
    assert types.count("bakeoff_done") == 2
    assert types.count("bakeoff_start") == 3  # 1 global + 2 per-model
    # deltas precede their model's done
    for model in ("model-a", "model-b"):
        first_delta = types.index("bakeoff_delta")
        last_done = len(types) - 1 - types[::-1].index("bakeoff_done")
        assert first_delta < last_done
    deltas_a = [e for e in events if e["type"] == "bakeoff_delta" and e["model"] == "model-a"]
    assert len(deltas_a) > 1
    assert all(e["delta"] for e in deltas_a)


def test_bakeoff_render_table(engine):
    async def _run():
        return await BakeOff(engine, ["model-a", "model-b"]).run("render me")

    entries = asyncio.run(_run())
    table = BakeOff.render(entries)
    assert "model-a" in table and "model-b" in table
    assert "tok/s" in table
    assert "meets 80 t/s" in table


# --------------------------------------------------------------------------
# Headless UI
# --------------------------------------------------------------------------


async def test_headless_repl_flow(engine, tmp_path):
    ui = HeadlessUI(engine, models=["model-a", "model-b"],
                    state=StateStore(str(tmp_path)))
    lines = await ui.repl([
        "/models",
        "/model model-b",
        "hello there",
        "/bakeoff compare",
        "/state save tester",
        "/quit",
    ])
    assert lines[0].startswith("SkeletonKey headless UI")
    assert "model-a" in lines[1] and "model-b" in lines[1]
    assert lines[2] == "model -> model-b"
    assert lines[3].strip()  # chat reply
    assert "model-a" in lines[4] and "model-b" in lines[4]  # bakeoff table
    assert lines[5] == "state saved for tester"


async def test_headless_state_roundtrip(engine, tmp_path):
    ui = HeadlessUI(engine, models=["model-a"], state=StateStore(str(tmp_path)))
    await ui.handle("/model model-a")
    assert await ui.handle("/state save alice") == "state saved for alice"
    ui2 = HeadlessUI(engine, models=["model-a", "model-b"],
                     state=StateStore(str(tmp_path)))
    loaded = await ui2.handle("/state load alice")
    assert loaded.startswith("state loaded:")
    assert "model-a" in loaded


async def test_headless_unknown_command(engine, tmp_path):
    ui = HeadlessUI(engine, state=StateStore(str(tmp_path)))
    assert await ui.handle("/warp") == "unknown command: /warp"
    assert await ui.handle("") == ""


# --------------------------------------------------------------------------
# Gradio (lazy)
# --------------------------------------------------------------------------


def test_build_app_without_gradio_raises(engine):
    with pytest.raises(BackendUnavailableError) as ei:
        build_app(engine)
    assert "gradio" in (ei.value.hint or "")


# --------------------------------------------------------------------------
# HTTP auth server
# --------------------------------------------------------------------------


@pytest.fixture
async def auth_server(clock):
    auth = NativeAuth(session_ttl=100, clock=clock)
    auth.add_user("alice", "wonderland")
    server = HttpServer(make_auth_server(auth), host="127.0.0.1", port=0)
    await server.start()
    try:
        yield auth, server
    finally:
        await server.stop()


def _call(server, method, path, body=None, token=None):
    conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                 headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, (json.loads(data) if data else None)


async def test_auth_http_login_me_logout(auth_server):
    auth, server = auth_server
    status, body = await asyncio.to_thread(_call, server, "POST", "/auth/login",
                                            {"username": "alice", "password": "wonderland"})
    assert status == 200 and body["token"]
    status, me = await asyncio.to_thread(_call, server, "GET", "/auth/me", token=body["token"])
    assert status == 200 and me["username"] == "alice"
    status, _ = await asyncio.to_thread(_call, server, "POST", "/auth/logout", token=body["token"])
    assert status == 204
    status, _ = await asyncio.to_thread(_call, server, "GET", "/auth/me", token=body["token"])
    assert status == 401


async def test_auth_http_bad_login(auth_server):
    _, server = auth_server
    status, body = await asyncio.to_thread(_call, server, "POST", "/auth/login",
                                            {"username": "alice", "password": "bad"})
    assert status == 401
    assert body["error"]["code"] == "auth_error"


async def test_auth_http_register(auth_server):
    auth, server = auth_server
    status, _ = await asyncio.to_thread(_call, server, "POST", "/auth/register",
                                         {"username": "bob", "password": "builder"})
    assert status == 201
    status, body = await asyncio.to_thread(_call, server, "POST", "/auth/login",
                                            {"username": "bob", "password": "builder"})
    assert status == 200


async def test_auth_http_device_flow(auth_server):
    auth, server = auth_server
    status, codes = await asyncio.to_thread(_call, server, "POST", "/device/code",
                                             {"username": "alice"})
    assert status == 200 and codes["device_code"] and codes["user_code"]
    # pending -> 424
    status, pending = await asyncio.to_thread(_call, server, "POST", "/device/token",
                                               {"device_code": codes["device_code"]})
    assert status == 424 and pending["status"] == "pending"
    status, _ = await asyncio.to_thread(_call, server, "POST", "/device/approve",
                                         {"user_code": codes["user_code"]})
    assert status == 200
    status, result = await asyncio.to_thread(_call, server, "POST", "/device/token",
                                              {"device_code": codes["device_code"]})
    assert status == 200 and result["token"]
    status, me = await asyncio.to_thread(_call, server, "GET", "/auth/me",
                                         token=result["token"])
    assert me["username"] == "alice"


async def test_auth_http_404(auth_server):
    _, server = auth_server
    status, _ = await asyncio.to_thread(_call, server, "GET", "/nope")
    assert status == 404
