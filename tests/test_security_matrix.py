"""P6 security pass: the red-team bypass matrix as executable tests.

PLAN §6 asks for the bypass attempts anyone would try against a tool with a
real boundary: `..`, absolute-external, symlink escape, device names, `\\\\?\\`,
env injection, CLIXML spoofing, spill-path traversal — plus fuzz-ish property
checks over random path shapes and sentinel payloads. Each is either a *deny*
with a named reason or an explicit documented *allow* (e.g. a contained `..`,
Win32 trailing-dot normalization). The gate: no attempt escapes without a
receipt.
"""

from __future__ import annotations

import json
import os
import random
import string

import pytest

from skeletonkey.core.envelope import _apply_budget
from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.fsx import sandbox as sandbox_mod
from skeletonkey.fsx.sandbox import PathSandbox, SandboxPolicy
from skeletonkey.shells.dialect import decode_clixml, parse_sentinel


def _sb(root, **policy_kw):
    return PathSandbox([str(root)], SandboxPolicy(**policy_kw))


def _deny(sb, path, intent="read"):
    try:
        sb.resolve(path, intent=intent)
        return None
    except SkeletonKeyError as exc:
        return exc.err.code


# ---------------------------------------------------------------- path bypass
def test_bypass_matrix_posix_paths_denied(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    root = tmp_path / "ws"
    root.mkdir()
    sb = _sb(root)
    cases = {
        "dotdot": "../../etc/passwd",
        "dotdot_nested": "src/../../outside.txt",
        "absolute_external": str(outside),
        "absolute_external_dot": str(outside) + "/.",
        "absolute_external_write": (str(outside), "write"),
        "nul_byte": "a\x00b",
    }
    for name, case in cases.items():
        path, intent = (case if isinstance(case, tuple) else (case, "read"))
        code = _deny(sb, path, intent=intent)
        assert code in ("SANDBOX_VIOLATION", "DENY_RULE", "BAD_ARGS"), (name, path, code)


def test_bypass_matrix_windows_shapes_denied(tmp_path):
    """UNC, `\\\\?\\`, device names, ADS and backslash traversal are Windows
    semantics; force the Windows branch so the string rules actually run
    anywhere (same technique as tests/test_sandbox.py)."""
    mod = sandbox_mod
    orig = mod._IS_WIN
    mod._IS_WIN = True
    try:
        sb = PathSandbox(["C:/work"])
        cases = {
            "backslash_traversal": "..\\..\\etc\\passwd",
            "double_backslash_unc": "\\\\server\\share\\secret.txt",
            "long_path_prefix": "\\\\?\\C:\\Windows\\system32\\calc.exe",
            "device_con": "C:/work/CON",
            "device_nul": "C:/work/nul.txt",
            "device_lpt": "C:/work/LPT1",
            "ads": "C:/work/data.txt:evil",
        }
        for name, path in cases.items():
            code = _deny(sb, path)
            assert code in ("SANDBOX_VIOLATION", "DENY_RULE", "BAD_ARGS"), (name, path, code)
        # trailing dot/space is Win32 normalization, not an escape: `dir. `
        # becomes `dir` under the same root (or is denied by the filter).
        try:
            res = sb.resolve("C:/work/dir. ", intent="read")
            assert res.real.replace("\\", "/").lower().endswith("c:/work/dir"), res.real
        except SkeletonKeyError:
            pass
    finally:
        mod._IS_WIN = orig


def test_bypass_matrix_internal_navigation_allowed(tmp_path):
    root = tmp_path / "ws"
    (root / "a" / "b").mkdir(parents=True)
    sb = _sb(root)
    # a contained `..` is not an escape: normalizing it must stay in-root
    res = sb.resolve("a/b/../b", intent="read")
    assert res.display.replace("\\", "/").endswith("a/b")
    assert sb.resolve("a/../a", intent="read").display.replace("\\", "/").endswith("a")


def test_bypass_matrix_uri_shapes_are_contained_literals(tmp_path):
    """`file:///etc/passwd` is not opened as a URI (no scheme handling): it is a
    literal relative name inside the root — surprising but never an escape."""
    root = tmp_path / "ws"
    root.mkdir()
    sb = _sb(root)
    res = sb.resolve("file:///etc/passwd", intent="read")
    assert os.path.realpath(res.real).startswith(os.path.realpath(str(root)) + os.sep)


def test_symlink_escape_receipt_names_the_link_target(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("s", encoding="utf-8")
    link = root / "l"
    try:
        os.symlink(str(outside), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    sb = _sb(root)
    try:
        sb.resolve("l", intent="read")
        raise AssertionError("symlink escape must be denied")
    except SkeletonKeyError as exc:
        assert exc.err.code in ("SANDBOX_VIOLATION", "DENY_RULE")
        details = exc.err.details or {}
        assert details.get("resolved") == str(outside), details


# ---------------------------------------------------------------- path fuzz
def test_random_path_shapes_never_escape(tmp_path):
    """Property: for a random path, resolve() either denies or produces a
    realpath inside a root. Never both, never silently outside."""
    root = tmp_path / "ws"
    root.mkdir()
    sb = _sb(root)
    rng = random.Random(1337)
    chunks = ["a", "..", ".", "b c", "x.yaml", "CON", "a..b", "..\\c", "-", "_", "\t", ""]
    allowed = 0
    for _ in range(400):
        parts = [rng.choice(chunks) for _ in range(rng.randint(1, 6))]
        path = os.path.join(*parts) if len(parts) > 1 else parts[0]
        try:
            res = sb.resolve(path, intent="read")
        except SkeletonKeyError:
            continue  # denied: fine
        allowed += 1
        real = os.path.realpath(res.real)
        in_root = real == os.path.realpath(str(root)) or real.startswith(
            os.path.realpath(str(root)) + os.sep)
        assert in_root, f"escape: {path!r} -> {real}"
    assert allowed >= 100, f"suspiciously few allows ({allowed}): fuzz is not exercising resolve"


# ------------------------------------------------------------- sentinel fuzz
def test_sentinel_parser_survives_spoofing():
    """Only this run's token counts; a fake sentinel in output is data. Random
    noise must never crash the parser."""
    token = "a1b2c3d4"
    good = f"start\n<<<SK1|{token}|rc=0|done=1|cwd=/work>>>\nend\n"
    parsed = parse_sentinel(good, token, dialect="bash")
    assert parsed.done is True and parsed.rc == 0
    assert parsed.head == "start\n"
    assert parsed.tail.startswith(f"<<<SK1|{token}|")

    spoof = "line <<<SK1|deadbeef|rc=9|done=1>>>\nx\n"
    parsed = parse_sentinel(spoof, token, dialect="bash")
    assert parsed.done is False and parsed.rc is None
    assert parsed.head == spoof, "a foreign token must stay data, not split output"

    rng = random.Random(7)
    alphabet = string.printable
    for _ in range(200):
        junk = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 400)))
        out = parse_sentinel(junk, token, dialect="bash")
        assert isinstance(out.done, bool)
        assert isinstance(out.head, str) and isinstance(out.tail, str)
        assert out.rc is None or isinstance(out.rc, int)


# ------------------------------------------------------------ CLIXML spoof
def test_clixml_decode_does_not_eat_non_xml_lines():
    """decode_clixml must expand real PowerShell error XML and leave ordinary
    text alone — including a line that merely looks like it starts with <Objs."""
    xml = (
        '#< CLIXML\n'
        '<Objs Version="1.0"><Obj S="Error"><S S="e">Get-Process : access denied</S></Obj></Objs>\n'
    )
    clean, errors, had = decode_clixml(xml)
    assert had
    assert any("access denied" in m for m in errors)
    assert "Get-Process : access denied" in clean

    plain = decode_clixml("just a line\nsecond line\n")
    assert plain[0] == "just a line\nsecond line\n" and plain[2] is False

    spoof = decode_clixml("<Objs<broken>>>  still text\nnon-xml line\n")
    assert "non-xml line" in spoof[0]


# ---------------------------------------------------------- env injection
def test_env_override_is_an_explicit_allow_with_receipt(tmp_path):
    """shell.run env= is a real feature (not a bypass): it must work, yet a
    cred-shaped value never reaches the audit trail (args redaction +
    result-preview pattern redaction) — visible in the ledger, not smuggled."""
    from skeletonkey.toolkit import build

    root = tmp_path / "ws"
    root.mkdir()
    tk = build(roots=[str(root)], cwd=str(root))
    token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    try:
        r = tk.engine.call("shell.run", {
            "script": "printf '%s' \"$API_TOKEN\"",
            "dialect": "bash",
            "env": {"API_TOKEN": token},
        })
        assert r.ok, r.error
        assert r.data["stdout"] == token  # the feature works
        rows = [row for row in tk.ledger.read(limit=20) if row.tool == "shell.run"]
        assert rows, "shell.run must be audited"
        assert token not in json.dumps(rows[-1].to_dict()), "env value must not leak into the ledger"
        assert any("secrets" in r for r in rows[-1].redacted), rows[-1].redacted
    finally:
        tk.close()


# ---------------------------------------------------------- spill traversal
def test_spill_writer_never_leaves_spill_dir(tmp_path):
    """An oversized result writes its spill artifact inside spill_dir; the path
    must be inside it (no ../ trick even with a hostile tool id)."""
    spill = tmp_path / "spill"
    payload = {"ok": True, "tool": "../../etc/passwd", "data": "x" * 500}
    _artifacts = _apply_budget(payload, 100, spill_dir=str(spill), tool="shell.run")[1]
    assert _artifacts
    for a in _artifacts:
        if getattr(a, "path", None):
            real = os.path.realpath(a.path)
            assert real.startswith(os.path.realpath(str(spill)) + os.sep), a.path


def test_truncated_payload_note_points_inside_spill_dir(tmp_path):
    """The 'full copy at ...' note is data; the path it advertises is inside
    spill_dir, so a host following it cannot be redirected outside."""
    spill = tmp_path / "spill"
    payload = {"ok": True, "tool": "fs.read", "data": "y" * 5000}
    out, artifacts = _apply_budget(payload, 200, spill_dir=str(spill), tool="fs.read")
    note = next(w for w in out["warnings"] if "full copy at" in w)
    assert str(spill) in note
    assert all(getattr(a, "path", None) is None
               or os.path.realpath(a.path).startswith(os.path.realpath(str(spill)) + os.sep)
               for a in artifacts)
