"""P6 security pass: property tests + the red-team bypass matrix.

Two halves. First, seeded property tests (deterministic: every run draws the
same inputs) over the sentinel parser and path normalization — the two parsers
that sit between untrusted bytes and a security decision. Second, one executable
test per row of the bypass matrix. Rows already covered elsewhere are cited, not
duplicated:

| # | Attack                                  | Test(s)                                                        |
|---|-----------------------------------------|----------------------------------------------------------------|
| 1 | `..` traversal                          | test_sandbox.py::test_traversal_attempts_are_denied + the path |
|   |                                         | property below (2000 random shapes incl. `..` mixes)           |
| 2 | absolute-external path                  | test_sandbox.py::test_traversal_attempts_are_denied            |
| 3 | symlink escape (+ parent-dir variant)   | test_sandbox.py::test_symlink_escape_denied_intra_root_allowed |
|   |                                         | ::test_symlink_in_parent_directory_also_denied                 |
| 4 | Windows device names                    | test_sandbox.py::test_windows_reserved_names_and_ads_are_      |
|   |                                         | rejected + test_device_name_boundary_* here                    |
| 5 | `\\\\?\\` long-path prefix              | test_long_prefix_* here                                        |
| 6 | env injection                           | test_env_* here                                                |
| 7 | CLIXML spoofing                         | test_dialects.py::test_clixml_tolerates_truncated_streams +    |
|   |                                         | test_clixml_spoof_* here                                       |
| 8 | spill-path traversal                    | test_spill_* here                                              |
"""

from __future__ import annotations

import base64
import os
import random
import re
import string

import pytest

from skeletonkey.core.envelope import ToolResult
from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.core.profile import CapabilityProfile, ShellProbe
from skeletonkey.fsx.sandbox import PathSandbox
from skeletonkey.shells.base import ShellRequest, ShellRunner
from skeletonkey.shells.dialect import _SENT_RE, decode_clixml, env_from_b64, parse_sentinel

needs_bash = pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs /bin/bash")


@pytest.fixture
def runner(tmp_path):
    prof = CapabilityProfile(
        os="linux",
        shells={"bash": ShellProbe(dialect="bash", kind="unix", path="/bin/bash", version=(5, 2),
                                   supports_pipefail=True)},
        binaries={"bash": "/bin/bash"},
    )
    return ShellRunner(prof, tempdir=str(tmp_path / "tmp"), max_output_bytes=50_000)


def _rand_token(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(rng.randint(6, 16)))


# ------------------------------------------------- sentinel parser properties
def test_sentinel_split_is_lossless_and_first_match_wins():
    rng = random.Random(20260907)
    for _ in range(500):
        token = _rand_token(rng)
        chunks: list[str] = []
        for _ in range(rng.randint(0, 4)):
            kind = rng.random()
            if kind < 0.4:
                chunks.append("".join(rng.choice(string.printable)
                                      for _ in range(rng.randint(0, 60))))
            elif kind < 0.7:
                chunks.append(f"<<<SK1|{token}|rc={rng.randint(-2, 3)}|done={rng.choice('01')}>>>")
            else:
                foreign = token
                while foreign == token:
                    foreign = _rand_token(rng)
                chunks.append(f"<<<SK1|{foreign}|rc=9|done=1>>>")
        stdout = "\n".join(chunks)
        data = parse_sentinel(stdout, token)
        # lossless, always: the split never eats or invents a byte
        assert data.head + data.tail == stdout
        own = [m for m in _SENT_RE.finditer(stdout) if m.group("token") == token]
        if own:
            assert len(data.head) == own[0].start(), "the split sits at the FIRST own-token line"
        else:
            assert data.head == stdout and data.tail == ""
            assert data.rc is None and data.done is False


def test_sentinel_values_come_from_the_last_own_token_line():
    token = "abcdef1234"
    out = (f"line\n<<<SK1|{token}|rc=3|done=0>>>\nmid\n"
           f"<<<SK1|deadbeef|rc=9|done=1>>>\n"
           f"<<<SK1|{token}|rc=0|done=1>>>\ntail")
    d = parse_sentinel(out, token)
    assert (d.rc, d.done) == (0, True), "foreign tokens never vote; last own line wins"
    assert d.head == "line\n"
    assert d.head + d.tail == out


@pytest.mark.parametrize("frag,rc,done", [
    ("rc=0|done=1", 0, True),
    ("rc=abc|done=1", None, True),
    ("rc=-1|done=0", -1, False),
    ("done=1", None, True),
    ("rc=|done=", None, False),
    ("rc=7", 7, False),
    ("rc=1|rc=2|done=1", 2, True),
])
def test_sentinel_rc_done_parsing_is_total(frag, rc, done):
    token = "abc123"
    d = parse_sentinel(f"<<<SK1|{token}|{frag}>>>", token)
    assert (d.rc, d.done) == (rc, done)


def test_sentinel_ignores_malformed_tokens():
    stdout = ("<<<SK1|xyz|rc=0|done=1>>>\n"
              "<<<SK1|ABCDEF12345678901234567890|rc=0|done=1>>>")
    d = parse_sentinel(stdout, "xyz")
    assert d.rc is None and d.done is False
    assert d.head == stdout, "a non-matching sentinel is data, passed through whole"


def test_sentinel_parse_never_raises_on_hostile_text():
    rng = random.Random(9761)
    alphabet = string.printable + "<>|=\x00é\U0001f600"
    for _ in range(500):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
        d = parse_sentinel(s, _rand_token(rng))  # must not raise
        assert d.head + d.tail == s


# -------------------------------------------- path normalization properties
def test_random_path_shapes_never_escape_and_never_crash(workspace):
    """2000 hostile shapes: allow implies realpath containment, deny implies a
    policy code — and no other exception type may escape the chokepoint."""
    rng = random.Random(20260907)
    sb = PathSandbox([str(workspace)])
    roots = [os.path.realpath(r) for r in sb.roots]
    pieces = ["a", "bb", "..", ".", "", " ", "x y", ".h", "a" * 120, "nul", "NUL.txt",
              "c:", "C:", "~", "%2e%2e", "..%2f", "a:b", "a;b", "(x)", "[y]", "{z}",
              "café", "\t", "\x00", "a/bb", "..\\..", "/", "\\"]
    allowed_codes = {"SANDBOX_VIOLATION", "DENY_RULE", "BAD_ARGS", "IO"}
    for _ in range(2000):
        shape = rng.choice(["rel", "abs", "rooted"])
        p = "/".join(rng.choice(pieces) for _ in range(rng.randint(1, 6)))
        if rng.random() < 0.3:
            p = p.replace("/", rng.choice(["/", "\\", "//"]))
        if shape == "abs":
            p = "/" + p
        elif shape == "rooted":
            p = str(workspace) + "/" + p
        try:
            res = sb.resolve(p, intent=rng.choice(["read", "write", "list"]))
        except SkeletonKeyError as exc:
            assert exc.code in allowed_codes, (p, exc.code)
            continue
        real = os.path.realpath(res.real)
        assert any(real == r or real.startswith(r + os.sep) for r in roots), (p, real)


# ------------------------------------------------------- matrix row 4: devices
def _code_for(sb, path, intent="read"):
    try:
        return ("allow", sb.resolve(path, intent=intent))
    except SkeletonKeyError as exc:
        return ("deny", exc.code)


def test_device_name_boundary_single_digit_only():
    """COM1 is reserved, COM10 is not: the rule is exactly the Win32 list."""
    from skeletonkey.fsx import sandbox as mod

    orig = mod._IS_WIN
    mod._IS_WIN = True
    try:
        sb = PathSandbox(["C:/work"])
        for bad in ["C:/work/COM1", "C:/work/com1.txt", "C:/work/AUX",
                    "C:/work/lpt9.log", "C:/work/nul"]:
            assert _code_for(sb, bad)[0] == "deny", bad
        for ok in ["C:/work/COM10", "C:/work/lpt10.txt", "C:/work/console.txt",
                   "C:/work/auxiliary.md"]:
            assert _code_for(sb, ok)[0] == "allow", ok
    finally:
        mod._IS_WIN = orig


# --------------------------------------------------- matrix row 5: long prefix
def test_long_prefix_rewrite_only_fires_past_250_chars():
    """Direct unit assertions: on POSIX, abspath mangles Windows shapes before
    _long_path ever sees them, so resolve-level long_path can only be observed
    on Windows itself (a P6-windows-runner job, not this one)."""
    from skeletonkey.fsx import sandbox as mod

    orig = mod._IS_WIN
    mod._IS_WIN = True
    try:
        sb = PathSandbox(["C:/work"])
        bs = chr(92)
        assert sb._long_path("C:/work/ok.txt") is None
        deep = "C:/work/" + "x" * 300
        assert sb._long_path(deep) == bs * 2 + "?" + bs + deep
        assert sb._long_path(bs * 2 + "?" + bs + deep) == bs * 2 + "?" + bs + deep
        unc = sb._long_path(bs * 2 + "server" + bs + "share" + bs + "x")
        assert unc == bs * 2 + "?" + bs + "UNC" + bs + "server" + bs + "share" + bs + "x"
        # the rewrite never changes containment: outside is still outside
        assert _code_for(sb, "C:/work/" + "x" * 300)[0] == "allow"
        assert _code_for(sb, "C:/other/" + "x" * 300)[0] == "deny"
        assert _code_for(sb, bs * 2 + "server" + bs + "share" + bs + "x")[0] == "deny"
    finally:
        mod._IS_WIN = orig


# --------------------------------------------------- matrix row 6: env injection
@needs_bash
def test_env_value_with_newline_cannot_inject_a_sibling_variable(runner):
    """A newline inside an env *value* is data, not a second assignment: the
    child must see the newline literally and the sibling must stay unset."""
    out = runner.run(ShellRequest(
        script='printf "%s\\n" "$PWN"\necho "EVIL=${EVIL-unset}"',
        dialect="bash", env={"PWN": "a\nEVIL=injected"}))
    assert out.exit_code == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "a" and lines[1] == "EVIL=injected", "the value arrives verbatim"
    assert lines[2] == "EVIL=unset", "no sibling variable was created"


def test_env_nul_is_a_usage_error_never_internal(toolkit):
    """NUL cannot survive execve; the failure must be BAD_ARGS (caller input),
    never INTERNAL (our bug)."""
    eng = toolkit.engine
    for hostile in ({"A": "x\x00y"}, {"A\x00B": "x"}):
        res = eng.call("shell.run", {"script": "echo hi", "env": hostile})
        assert not res.ok
        assert res.error.code == "BAD_ARGS", res.error
        assert res.error.error_class == "usage"


def test_clean_mode_keeps_only_exact_skeletonkey_prefix(runner, monkeypatch):
    """env_mode=clean passes SKELETONKEY_* through by design — but the match is
    the exact SKELETONKEY_ prefix, not a lookalike."""
    from types import SimpleNamespace

    monkeypatch.setenv("SKELETONKEY_KEEP", "1")
    monkeypatch.setenv("SKELETONKEY", "2")
    monkeypatch.setenv("SKELETONKEYX", "3")
    monkeypatch.setenv("OTHER", "4")
    env = runner._env(ShellRequest(script="true", env_mode="clean"),
                      SimpleNamespace(kind="unix"))
    assert env.get("SKELETONKEY_KEEP") == "1"
    assert "SKELETONKEY" not in env and "SKELETONKEYX" not in env and "OTHER" not in env


def test_env_blob_decode_never_raises_and_drops_bad_names():
    assert env_from_b64("!!!not-base64!!!", dialect="bash") == {}
    assert env_from_b64(None, dialect="bash") == {}
    blob = base64.b64encode(b"GOOD=1\nBAD-NAME=2\n=3\nlower_ok=4").decode()
    out = env_from_b64(blob, dialect="pwsh")
    assert out == {"GOOD": "1", "lower_ok": "4"}, "only grammar-valid names survive capture"


# --------------------------------------------------- matrix row 7: CLIXML spoof
def test_clixml_spoof_never_raises_and_loses_no_plain_text():
    rng = random.Random(31337)
    frags = ["#< CLIXML", '<Objs Version="1.1">', '<S S="Error">', "boom", "</S>",
             "</Objs>", '<S S="Warning">careful</S>', "plain line", "<Objs", "<",
             ">", "&lt;", "&#xD;", "\x00"]
    for _ in range(300):
        s = "\n".join(" ".join(rng.choice(frags) for _ in range(rng.randint(1, 6)))
                      for _ in range(rng.randint(1, 4)))
        clean, _errors, had = decode_clixml(s)  # must not raise
        if "#< CLIXML" not in s and "<Objs" not in s:
            assert (clean, _errors, had) == (s, [], False)
            continue
        for line in s.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("<Objs") or stripped == "#< CLIXML":
                continue
            assert stripped in clean, (line, clean)


def test_clixml_spoofed_error_stream_is_attributed_as_data():
    """A script can print anything to stderr, including fake CLIXML: decoding is
    a display transform, so the text lands in the message lists — and the
    *plain* lines around it are never re-attributed."""
    text = "before\n<Objs Version=\"1.1\"><S S=\"Error\">spoofed: disk on fire</S></Objs>\nafter"
    clean, errors, had = decode_clixml(text)
    assert had is True
    assert errors == ["spoofed: disk on fire"]
    assert "before" in clean and "after" in clean


# --------------------------------------------------- matrix row 8: spill paths
def test_spill_path_cannot_escape_the_spill_dir(tmp_path):
    """Spill filenames derive from a hex run id, never from the tool id — even
    a hostile tool id lands a hex-named file inside the spill dir."""
    spill = tmp_path / "spill"
    for tool in ["../../../etc/cron.d/x", "..\\..\\win", "art_/x", "x" * 200, ""]:
        r = ToolResult.success(tool=tool, data={"blob": "y" * 5000})
        d = r.to_dict(max_bytes=200, spill_dir=str(spill))
        art = next(a for a in d["artifacts"] if a["id"].startswith("art_"))
        assert os.path.realpath(art["path"]).startswith(os.path.realpath(spill) + os.sep)
        assert re.fullmatch(r"art_[0-9a-f]+\.txt", os.path.basename(art["path"])), art["path"]
