"""The publishing subsystem: write-only credential store + placeholder injection.

Driven two ways the way a host drives it:

* engine-level: the ``pub.*`` tools through ``engine.call`` (schema, data shape,
  the no-partial-write rule, undoability, ledger redaction of secret args);
* store-level: ``core.publish.PublishStore`` directly (persistence, permissions,
  validation, the write-only contract at the API level).

The wire-level test lives in ``tests/test_mcp_stdio.py`` (house rule: a feature
that only works when called from Python is not done).
"""

from __future__ import annotations

import json
import os

import pytest

from skeletonkey.core.config import Config
from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.core.publish import (
    KINDS,
    MARKER_RE,
    PublishStore,
    find_markers_in_text,
    replace_markers,
)
from skeletonkey.core.redact import redact_obj
from skeletonkey.toolkit import build


def call(engine, tool, /, **args):
    """Positional-only `tool`: some tools take an argument literally named `tool`."""
    return engine.call(tool, args)


@pytest.fixture
def pub_toolkit(workspace, tmp_path):
    """The shared workspace, but with the store pinned to a temp path and writes
    auto-approved (pub.inject / pub.store_delete are write/destructive)."""
    store_path = tmp_path / "store.json"
    cfg = Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)],
        "state": {"dir": str(workspace / ".sk")},
        "shell": {"tempdir": str(workspace / ".sk" / "shell")},
        "publish": {"store_path": str(store_path)},
        "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                   "confirm_destructive": False},
        "log_level": "ERROR",
    })
    tk = build(config=cfg)
    try:
        yield tk
    finally:
        tk.close()


# ------------------------------------------------------------------ the store
def test_store_put_persists_and_metas_mask(tmp_path):
    s = PublishStore(tmp_path / "st.json")
    s.put("pypi.token", "token", "pypi-pypi-abc", note="ci")
    meta = s.meta("pypi.token")
    assert meta["kind"] == "token" and meta["note"] == "ci"
    assert "pypi-pypi-abc" not in json.dumps(meta), "raw value must never leave the store API"
    assert meta["value_masked"]

    # persistence: a fresh instance over the same file sees the entry
    s2 = PublishStore(tmp_path / "st.json")
    assert s2.has("pypi.token")
    assert s2.value("pypi.token") == "pypi-pypi-abc"  # internal read path exists...

    # ...but only as a write-only contract: list metas stay masked
    assert "pypi-pypi-abc" not in json.dumps(s2.metas())


def test_store_file_is_0600(tmp_path):
    s = PublishStore(tmp_path / "st.json")
    s.put("a.b", "token", "x" * 20)
    mode = os.stat(s.path).st_mode & 0o777
    assert mode == 0o600, f"store file must be owner rw only, got {oct(mode)}"


def test_store_update_keeps_created_and_moves_updated(tmp_path):
    s = PublishStore(tmp_path / "st.json")
    s.put("a", "token", "v1")
    created = s.meta("a")["created"]
    s.put("a", "token", "v2")
    m = s.meta("a")
    assert m["created"] == created
    assert s.value("a") == "v2"


def test_store_delete_is_irreversible_and_reports_so(tmp_path):
    s = PublishStore(tmp_path / "st.json")
    s.put("a", "token", "v")
    out = s.delete("a")
    assert out["deleted"] is True and "irreversible" in out["note"]
    assert not s.has("a")
    with pytest.raises(SkeletonKeyError):
        s.value("a")


def test_store_rejects_bad_ids_and_kinds(tmp_path):
    s = PublishStore(tmp_path / "st.json")
    for bad in ("", "UPPER", "has space", "a" * 65, "a..b", "a{{b}", "..leading", "-dash"):
        with pytest.raises(SkeletonKeyError):
            s.put(bad, "token", "v")
    with pytest.raises(SkeletonKeyError):
        s.put("ok.id", "not_a_kind", "v")
    for kind in KINDS:
        s.put("k." + kind, kind, "v")  # every documented kind is accepted
    assert len(s.ids()) == len(KINDS)


def test_store_rejects_empty_value(tmp_path):
    s = PublishStore(tmp_path / "st.json")
    with pytest.raises(SkeletonKeyError):
        s.put("a", "token", "")


def test_store_corrupt_file_is_a_clear_error(tmp_path):
    p = tmp_path / "st.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(SkeletonKeyError):
        PublishStore(p)


def test_store_path_default_is_outside_the_workspace(tmp_path, monkeypatch):
    """The wall: with no override the store lands under the *user* config dir,
    never under the workspace root the fs sandbox is rooted at."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("APPDATA", raising=False)
    from skeletonkey.core.config import _user_dir
    default = os.path.join(_user_dir(), "publish", "store.json")
    ws = str(tmp_path / "ws")
    assert not default.startswith(ws), "the store must not be reachable via fs.*"


# ---------------------------------------------------------- placeholder engine
def test_markers_located_with_exact_file_line_column(tmp_path):
    text = "lead {{PUB.a.token}} mid\ntwo {{PUB.b}} and {{PUB.a.token}} again\nplain line\n"
    s = PublishStore(tmp_path / "st.json")
    s.put("a.token", "token", "X")
    # b is missing on purpose
    ms = find_markers_in_text(text, "f.txt", s)
    assert [(m.line, m.column, m.id) for m in ms] == [(1, 6, "a.token"), (2, 5, "b"), (2, 19, "a.token")]
    assert [m.bound for m in ms] == [True, False, True]
    assert ms[0].marker == "{{PUB.a.token}}"


def test_marker_grammar_rejects_bad_ids():
    assert MARKER_RE.findall("{{PUB.bad ID}} {{PUB.}} {{PUB.9ok}} {{PUB.ok_id-1.x}}") \
        == ["9ok", "ok_id-1.x"]


def test_replace_bindings_remap_markers(tmp_path):
    s = PublishStore(tmp_path / "st.json")
    s.put("old.token", "token", "OLD")
    s.put("new.token", "token", "NEW")
    text = "x={{PUB.old.token}}"
    # default: marker id IS the store id
    new, _, missing = replace_markers(text, s)
    assert new == "x=OLD" and missing == []
    # bindings: remap a marker to a different store entry
    new, _, missing = replace_markers("y={{PUB.new.token}}", s, bindings={"new.token": "old.token"})
    assert new == "y=OLD" and missing == []
    # missing: reported, text untouched
    new, _, missing = replace_markers("z={{PUB.absent}}", s)
    assert new == "z={{PUB.absent}}" and missing == ["absent"]


# ------------------------------------------------------------ pub.* via engine
def test_store_put_result_never_carries_the_value(pub_toolkit):
    r = call(pub_toolkit.engine, "pub.store_put", id="pypi.token", kind="token",
             value="pypi-pypi-SECRET-123", note="ci")
    assert r.ok
    blob = json.dumps(r.to_dict())
    assert "pypi-pypi-SECRET-123" not in blob, "the value must not echo back in the envelope"
    assert r.data["stored"]["id"] == "pypi.token"


def test_store_put_rejects_bad_id_and_kind(pub_toolkit):
    assert not call(pub_toolkit.engine, "pub.store_put", id="Nope!", kind="token",
                    value="x").ok
    assert not call(pub_toolkit.engine, "pub.store_put", id="ok.id", kind="banana",
                    value="x").ok


def test_store_list_is_metadata_only(pub_toolkit):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="pypi.token", kind="token", value="secret-a")
    call(eng, "pub.store_put", id="github.password", kind="password", value="secret-b")
    r = call(eng, "pub.store_list")
    assert r.ok and r.data["count"] == 2
    blob = json.dumps(r.data)
    assert "secret-a" not in blob and "secret-b" not in blob
    # kind filter
    r2 = call(eng, "pub.store_list", kind="password")
    assert r2.data["count"] == 1 and r2.data["entries"][0]["id"] == "github.password"
    # unknown kind is a usage error, not a silent empty list
    assert not call(eng, "pub.store_list", kind="banana").ok


def test_placeholders_report_exact_locations_and_binding_state(pub_toolkit, workspace):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="pypi.token", kind="token", value="v1")
    (workspace / "deploy.env").write_text("A={{PUB.pypi.token}}\nB={{PUB.ghost.id}}\n",
                                          encoding="utf-8")
    r = call(eng, "pub.placeholders", path=".")
    assert r.ok
    d = r.data
    markers = {(m["file"], m["line"], m["column"], m["id"]): m["status"]
               for m in d["markers"] if m["id"] in ("pypi.token", "ghost.id")}
    assert markers[("deploy.env", 1, 3, "pypi.token")] == "bound"
    assert markers[("deploy.env", 2, 3, "ghost.id")] == "missing"
    assert d["missing_ids"] == ["ghost.id"]
    assert d["ready_to_publish"] is False


def test_inject_refuses_unbound_markers_and_writes_nothing(pub_toolkit, workspace):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="pypi.token", kind="token", value="v1")
    f1 = workspace / "a.env"
    f2 = workspace / "b.env"
    f1.write_text("A={{PUB.pypi.token}}\n", encoding="utf-8")
    f2.write_text("B={{PUB.ghost.id}}\n", encoding="utf-8")
    r = call(eng, "pub.inject", path=".")
    assert not r.ok and r.error.code == "ENOENT"
    assert "ghost.id" in json.dumps(r.error.details)
    # no partial publish: NEITHER file changed
    assert f1.read_text() == "A={{PUB.pypi.token}}\n"
    assert f2.read_text() == "B={{PUB.ghost.id}}\n"


def test_inject_dry_run_reports_the_plan_without_writing(pub_toolkit, workspace):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="pypi.token", kind="token", value="v1")
    f = workspace / "plan.env"
    f.write_text("A={{PUB.pypi.token}}\n", encoding="utf-8")
    r = call(eng, "pub.inject", path="plan.env", dry_run=True)
    assert r.ok and r.data["dry_run"] is True
    assert f.read_text() == "A={{PUB.pypi.token}}\n", "dry run must not write"
    entry = next(e for e in r.data["files"] if e["path"] == "plan.env")
    assert entry["changed"] is True
    assert entry["markers"][0]["file"] == "plan.env"


def test_inject_writes_through_the_journal_and_undoes(pub_toolkit, workspace):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="pypi.token", kind="token", value="pypi-pypi-VALUE")
    f = workspace / "live.env"
    original = "TOKEN={{PUB.pypi.token}}\n"
    f.write_text(original, encoding="utf-8")
    r = call(eng, "pub.inject", path="live.env")
    assert r.ok and r.data["files_written"] == 1
    assert f.read_text() == "TOKEN=pypi-pypi-VALUE\n"
    token = r.data["written"][0]["undo_token"]
    assert token, "each written file carries its own undo token"
    # the undo block reverts exactly this call's writes (per-call task id)
    assert r.data["undo"]["tool"] == "fs.undo_task"
    u = call(eng, "fs.undo_task", task_id=r.data["undo"]["args"]["task_id"])
    assert u.ok
    assert f.read_text() == original
    # re-injecting a file with no markers writes nothing
    f.write_text("no markers here\n", encoding="utf-8")
    r2 = call(eng, "pub.inject", path="live.env")
    assert r2.ok and r2.data["files_written"] == 0


def test_inject_preserves_crlf(pub_toolkit, workspace):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="x.y", kind="token", value="V")
    f = workspace / "crlf.env"
    f.write_bytes(b"A={{PUB.x.y}}\r\nB=2\r\n")
    r = call(eng, "pub.inject", path="crlf.env")
    assert r.ok
    assert f.read_bytes() == b"A=V\r\nB=2\r\n", "CRLF files must stay CRLF"


def test_inject_bindings_remap_to_other_store_entries(pub_toolkit, workspace):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="live.token", kind="token", value="LIVE")
    f = workspace / "bind.env"
    f.write_text("T={{PUB.staging.token}}\n", encoding="utf-8")
    # the marker points at an id that does not exist - but bindings remap it
    r = call(eng, "pub.inject", path="bind.env", bindings={"staging.token": "live.token"})
    assert r.ok
    assert f.read_text() == "T=LIVE\n"


def test_secret_arg_never_reaches_the_ledger(pub_toolkit):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="ledger.check", kind="token", value="LEDGER-MUST-NOT-SEE-THIS")
    with open(pub_toolkit.config.state.dir + "/ledger.ndjson", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    put_rows = [row for row in rows if row["tool"] == "pub.store_put"]
    assert put_rows, "the store_put call must be journaled"
    blob = json.dumps(rows)
    assert "LEDGER-MUST-NOT-SEE-THIS" not in blob, "the secret must not appear in ANY ledger row"
    args_blob = put_rows[-1]["args"]["_json"]
    assert '"value"' in args_blob and "***REDACTED***" in args_blob


def test_store_delete_via_engine_is_destructive_and_reports(pub_toolkit):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="doomed", kind="token", value="v")
    r = call(eng, "pub.store_delete", id="doomed")
    assert r.ok and r.data["deleted"] is True
    r2 = call(eng, "pub.store_list")
    assert r2.data["count"] == 0
    # deleting again is NOT_FOUND, not a silent success
    assert not call(eng, "pub.store_delete", id="doomed").ok


# --------------------------------------------------------------- knowledge KBs
def test_platforms_listing_and_detail():
    from skeletonkey import publish_data
    for key in ("google_play", "apple_appstore", "github", "pypi", "npm", "custom"):
        assert key in publish_data.PLATFORMS, f"missing platform {key}"
        p = publish_data.PLATFORMS[key]
        # `custom`'s console is the user's own domain, so docs URL is the check there
        assert (p["console"].startswith("http") or p["docs"].startswith("http")) and p["steps"]
        for cred in p["credentials"]:
            assert cred["kind"] in KINDS, f"{key} credential kind must be a store kind"


def test_payments_listing_and_detail():
    from skeletonkey import publish_data
    for key in ("stripe", "paddle", "google_play_billing", "apple_iap"):
        assert key in publish_data.PAYMENTS
        e = publish_data.PAYMENTS[key]
        assert e["console"].startswith("http") and e["steps"]
    # the honest note: Apple Pay checkout is a PSP feature
    apple = publish_data.PAYMENTS["apple_iap"]["notes"].lower()
    assert "apple pay" in apple and "psp" in apple


def test_packaging_listing_and_detail():
    from skeletonkey import publish_data
    for key in ("pypi", "github_release", "windows_installer", "msi", "scoop",
                "chocolatey", "winget", "homebrew", "self_hosted"):
        assert key in publish_data.PACKAGING
        e = publish_data.PACKAGING[key]
        assert e["steps"] and e["verify"], f"{key} needs steps and a verification path"


def test_kb_tools_via_engine(pub_toolkit):
    eng = pub_toolkit.engine
    r = call(eng, "pub.platforms")
    assert r.ok and r.data["count"] >= 6
    r = call(eng, "pub.platforms", name="pypi")
    assert r.ok and r.data["console"] == "https://pypi.org"
    assert not call(eng, "pub.platforms", name="nope").ok
    r = call(eng, "pub.payments", provider="stripe")
    assert r.ok and any("webhook" in s.lower() for s in r.data["steps"])
    r = call(eng, "pub.packaging", target="winget")
    assert r.ok and "winget-pkgs" in json.dumps(r.data)


def test_testers_plan_is_executable_and_secret_free(pub_toolkit):
    eng = pub_toolkit.engine
    call(eng, "pub.store_put", id="pypi.token", kind="token", value="SHOULD-NOT-APPEAR")
    r = call(eng, "pub.testers", platform="pypi", packaging="pypi", version="1.2.3")
    assert r.ok
    plan = r.data
    assert plan["version"] == "1.2.3" and len(plan["steps"]) >= 4
    blob = json.dumps(plan)
    assert "SHOULD-NOT-APPEAR" not in blob
    assert "{{PUB." in blob or "pub.inject" in blob, "plans reference the store, never values"
    phases = {s["phase"] for s in plan["steps"]}
    assert {"preflight", "build", "publish", "verify"} <= phases
    for s in plan["steps"]:
        assert s["accept"], "every step needs an acceptance line"
        assert s["on_fail"], "every step needs an on-fail behavior"


def test_redact_obj_masks_bare_value_but_not_value_masked():
    out = redact_obj({"value": "raw-secret", "value_masked": "ab…cd(9)", "values": ["x"],
                      "note": "value"})
    assert out["value"] == "***REDACTED***"
    assert out["value_masked"] == "ab…cd(9)", "display masks must survive the backstop"
    assert out["values"] == ["x"]
    assert out["note"] == "value", "prose is not a key"
