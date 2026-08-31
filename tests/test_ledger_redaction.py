"""Audit chain + redaction.

These two modules are the "we can explain what the agent did" half of the design,
and both are pure enough to test exhaustively. The redaction cases are not
cosmetic: each one encodes a shape that used to slip through (or crash).
"""

from __future__ import annotations

import json

import pytest

from skeletonkey.core.ledger import Ledger, LedgerEntry
from skeletonkey.core.redact import _PATTERNS, looks_secrety, redact_env, redact_obj, redact_text


# ------------------------------------------------------------------ redaction
def test_pattern_table_is_well_formed():
    """redact_text unpacks every row; a short row once made *all* redaction raise,
    which is the kind of bug a 2-line structural check permanently retires."""
    for row in _PATTERNS:
        assert len(row) == 4, f"{row[0]!r} needs (label, regex, name, value_group)"
        label, rx, name, grp = row
        assert isinstance(rx.pattern, str) and name == name.upper()
        assert grp is None or 1 <= grp <= rx.groups
        assert rx.groups >= (grp or 0), f"{label} captures fewer groups than it redacts"


@pytest.mark.parametrize(
    "raw,label",
    [
        ("AKIAIOSFODNN7EXAMPLE", "aws_key"),
        ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "gh_token"),
        ("github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz_1234567890", "github_pat"),
        ("xoxb-1234567890-abcdefghij", "slack"),
        ("sk-ant-api03-abcdefghijklmnopqrstuvwx", "anthropic"),
        ("sk-proj-abcdefghijklmnopqrstuvwx", "openai"),
        ("AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567", "google"),
        ("hf_abcdefghijklmnopqrstuvwx", "hf"),
        ("sk_live_abcdefghijklmnopqrstuvwx", "stripe"),
    ],
)
def test_each_token_family_is_caught(raw, label):
    out, hits = redact_text(f"key = {raw}")
    assert label in hits, hits
    assert raw not in out


def test_url_password_is_dropped_but_username_kept():
    out, hits = redact_text("psql postgres://admin:s3cr3t@db.internal:5432/app")
    assert "s3cr3t" not in out
    assert "admin" in out, "the username is not the secret and stays useful"
    assert "url_creds" in hits


@pytest.mark.parametrize(
    "raw,value",
    [('{"api_key": "supersecretvalue", "user": "bob"}', "supersecretvalue"),
     ("API_KEY=abcdef123456", "abcdef123456"),
     ('password = "hunter2hunter2"', "hunter2hunter2"),
     ("client_secret: 'abcdefghijklmnop'", "abcdefghijklmnop"),
     ("Session-Token = zzzyyywwwvvvuuuttt", "zzzyyywwwvvvuuuttt"),
     ("Authorization: Bearer abcdefghijklmnop0123456789", "abcdefghijklmnop0123456789"),
     ("curl -H 'authorization: Bearer eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJ' https://api", "SflKxwRJ"),
     ('--password "sup3rs3cr3t"', "sup3rs3cr3t")],
)
def test_the_secret_itself_is_gone_whatever_the_punctuation(raw, value):
    """Not "annotated", not "partially masked" - the bytes must not survive."""
    out, hits = redact_text(raw)
    assert hits, raw
    assert value not in out
    assert "***" in out, "mask visibly - silently dropping text is its own bug"


def test_private_key_block_is_removed_not_just_its_header():
    body = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz"
    raw = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{body}\n-----END OPENSSH PRIVATE KEY-----\n"
    out, hits = redact_text(raw)
    assert "pem" in hits
    assert body not in out, "the key material is the secret; the header alone is not enough"
    assert "BEGIN" not in out


def test_clean_text_is_returned_unchanged():
    text = "def handler(request):\n    return {'port': 8080}\n"
    out, hits = redact_text(text)
    assert out == text and hits == []


def test_redacted_output_is_still_valid_json():
    out, hits = redact_text('{"token": "abcdefghij123456", "n": 2}')
    parsed = json.loads(out)
    assert parsed["n"] == 2, "an audit line that will not parse is worse than none"
    assert "abcdefghij123456" not in out and hits == ["kv_secret"]

    for raw in ['{"api_token": "zzzz1234567890", "x": 1}', "mysql --password=hunter2 --user=root",
                "API_TOKEN=sk-super-secret-value"]:
        red, hit = redact_text(raw)
        assert "***REDACTED***" in red and hit, raw
        if raw.startswith("{"):
            assert json.loads(red)["x"] == 1

    # ...and things that merely look like assignments stay untouched
    for safe in ["git clone https://github.com/foo/bar.git", "print(1 + 1)", "timeout=30"]:
        red, hit = redact_text(safe)
        assert red == safe and hit == [], safe


def test_redact_obj_masks_secret_looking_keys_entirely():
    d = {"access_token": "abc", "path": "/tmp/x", "nested": {"password": "p@ss"}}
    out = redact_obj(d)
    assert out["access_token"] == "***REDACTED***"
    assert out["path"] == "/tmp/x"
    assert out["nested"]["password"] == "***REDACTED***"


def test_redact_obj_survives_odd_types():
    out = redact_obj({"a": [1, 2.5, True, None, {"b": "ghp_" + "A" * 40}], "n": None})
    assert "ghp_" not in json.dumps(out)
    assert out["a"][2] is True and out["a"][3] is None and out["n"] is None


def test_redact_env_keeps_names_drops_values():
    env = {"PATH": "/usr/bin", "AZURE_DEVOPS_TOKEN": "supersecret", "AWS_SECRET_ACCESS_KEY": "x" * 40}
    out = redact_env(env)
    assert out["PATH"] == "/usr/bin"
    assert out["AZURE_DEVOPS_TOKEN"] == "***11B***"


@pytest.mark.parametrize("line,expected", [
    ("password = x", True), ("# just a comment", False), ("token: abc", True),
    ("the password of the account", False),
])
def test_looks_secrety(line, expected):
    assert looks_secrety(line) is expected


# ------------------------------------------------------------------ ledger
def test_chain_is_append_only_and_verifiable(tmp_path):
    led = Ledger(str(tmp_path / "l.ndjson"))
    for i in range(5):
        led.append(tool="fs.read", args={"i": i}, ok=i % 2 == 0, duration_ms=i,
                   error_code=None if i % 2 == 0 else "IO", risk="read")
    led.close()
    res = Ledger(str(tmp_path / "l.ndjson")).verify()
    assert res["valid"] is True and res["lines"] == 5 and res["orphans"] == 0


def test_tampering_with_one_line_breaks_the_chain(tmp_path):
    path = tmp_path / "l.ndjson"
    led = Ledger(str(path))
    for i in range(3):
        led.append(tool="fs.read", args={"i": i}, ok=True, duration_ms=1)
    led.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["ok"] = False          # rewrite history: claim a failure as a success
    lines[1] = json.dumps(row, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = Ledger(str(path)).verify()
    assert res["valid"] is False
    assert res["broken_at"]["line"] == 2 and res["broken_at"]["reason"] == "digest mismatch"


def test_entries_round_trip_even_when_fields_are_empty(tmp_path):
    """`to_dict` omits empties to keep the file lean; reading must still work."""
    led = Ledger(str(tmp_path / "l.ndjson"))
    led.append(tool="t", args=None, ok=True, duration_ms=0)
    led.append(tool="t", args={}, ok=False, duration_ms=0, error_code="IO")
    led.close()
    rows = list(Ledger(str(tmp_path / "l.ndjson")).read(limit=10))
    assert [(r.seq, r.ok, r.duration_ms, r.error_code) for r in rows] == [(1, True, 0, None), (2, False, 0, "IO")]


def test_torn_tail_is_recovered_not_fatal(tmp_path):
    path = tmp_path / "l.ndjson"
    led = Ledger(str(path))
    led.append(tool="fs.read", args={"a": 1}, ok=True, duration_ms=2)
    led.close()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"seq":2,"tool":"fs.read","ok":tr')  # killed mid-write
    recovered = Ledger(str(path))
    assert [r.seq for r in recovered.read(limit=10)] == [1], "the half line must be dropped, not crash"
    recovered.append(tool="fs.read", args={"b": 2}, ok=True, duration_ms=3)
    recovered.close()
    after = Ledger(str(path))
    assert after.verify()["valid"] is True, "appending after a torn tail must keep the chain intact"
    assert [r.seq for r in after.read(limit=10)] == [1, 2]


def test_disabled_ledger_writes_nothing(tmp_path):
    led = Ledger(str(tmp_path / "off.ndjson"), enabled=False)
    led.append(tool="t", args={"x": 1}, ok=True, duration_ms=1)
    led.close()
    assert not (tmp_path / "off.ndjson").exists()


def test_secrets_in_results_are_redacted_in_the_ledger(tmp_path):
    path = tmp_path / "l.ndjson"
    led = Ledger(str(path))
    led.append(tool="fs.read", args={"path": ".env"}, ok=True, duration_ms=1,
               result={"ok": True, "data": {"content": "API_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"}})
    led.close()
    raw = path.read_text(encoding="utf-8")
    assert "ghp_ABCDEFGHIJ" not in raw, "the ledger is a copy of what the model saw; keep it clean"
    row = json.loads(raw)
    assert "secrets:" in " ".join(row["redacted"])
    assert row["result_digest"], "the digest must survive redaction so tampering is still detectable"


def test_args_are_stored_truncated_and_digest_is_stable(tmp_path):
    led = Ledger(str(tmp_path / "l.ndjson"), max_arg_bytes=40)
    a = led.append(tool="t", args={"blob": "y" * 5000}, ok=True, duration_ms=1)
    b = led.append(tool="t", args={"blob": "y" * 5000}, ok=True, duration_ms=1)
    assert a.args_digest == b.args_digest
    assert "args_truncated" in a.redacted
    led.close()


def test_read_filters_only_show_what_was_asked(tmp_path):
    led = Ledger(str(tmp_path / "l.ndjson"))
    led.append(tool="fs.read", args={}, ok=True, duration_ms=1)
    led.append(tool="fs.write", args={}, ok=False, duration_ms=2, error_code="IO")
    led.append(tool="fs.read", args={}, ok=False, duration_ms=3, error_code="ENOENT")
    assert [r.tool for r in led.read(tool="fs.read")] == ["fs.read", "fs.read"]
    assert [(r.tool, r.error_code) for r in led.read(only_failures=True)] == [
        ("fs.write", "IO"), ("fs.read", "ENOENT")]
    assert [r.seq for r in led.read(limit=1)] == [3], "limit means the newest N"
    led.close()


def test_stats_summarise_for_the_doctor(tmp_path):
    led = Ledger(str(tmp_path / "l.ndjson"))
    for ok in (True, True, False):
        led.append(tool="fs.read", args={}, ok=ok, duration_ms=4, error_code=None if ok else "IO")
    st = led.stats()
    led.close()
    assert st["calls"] == 3 and st["failures"] == 1
    assert st["per_tool"]["fs.read"]["failures"] == 1


def test_ledgerentry_defaults_are_self_consistent():
    e = LedgerEntry()
    assert e.seq == 0 and e.ok is True and e.duration_ms == 0
    assert LedgerEntry(**json.loads(json.dumps(e.to_dict()))) == e
