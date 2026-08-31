"""Path sandbox is the security boundary: these are adversarial cases, not happy paths."""

from __future__ import annotations

import os
import sys

import pytest

from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.fsx.sandbox import PathSandbox, SandboxPolicy, _glob_to_re, detect_case_sensitivity


def code_for(sb, path, intent="read"):
    try:
        return ("allow", sb.resolve(path, intent=intent))
    except SkeletonKeyError as exc:
        return ("deny", exc.code)


def test_relative_paths_resolve_against_primary_root(workspace):
    sb = PathSandbox([str(workspace)])
    _verdict, res = code_for(sb, "README.md")
    assert res.display == "README.md"
    assert res.exists and res.is_file


def test_traversal_attempts_are_denied(workspace):
    sb = PathSandbox([str(workspace)])
    for evil in ["../../etc/passwd", "/etc/passwd", "src/../../README.md",
                 str(workspace.parent / "outside.txt")]:
        assert code_for(sb, evil)[0] == "deny", evil
    # but a *contained* .. is fine
    assert code_for(sb, "src/../README.md")[0] == "allow"


@pytest.mark.skipif(sys.platform == "win32", reason="posix absolute path shape")
def test_nul_byte_and_control_chars(workspace):
    sb = PathSandbox([str(workspace)])
    assert code_for(sb, "README.md\x00.txt")[0] == "deny"


def test_symlink_escape_denied_intra_root_allowed(workspace):
    link_out = workspace / "link-out"
    outside = workspace.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        os.symlink(str(outside), link_out)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    sb = PathSandbox([str(workspace)])
    assert code_for(sb, "link-out")[1] in ("SANDBOX_VIOLATION", "DENY_RULE")
    inside = workspace / "src" / "pkg" / "linked.py"
    try:
        os.symlink("mod.py", inside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    verdict, res = code_for(sb, "src/pkg/linked.py")
    assert verdict == "allow" and "symlink" in " ".join(res.notes)


def test_symlink_in_parent_directory_also_denied(workspace):
    """The sneaky one: link *inside* root whose *parent* escapes."""
    outside_dir = workspace.parent / "outside-dir"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "s.txt").write_text("x", encoding="utf-8")
    try:
        os.symlink(str(outside_dir), workspace / "pdir")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    sb = PathSandbox([str(workspace)])
    assert code_for(sb, "pdir/s.txt")[0] == "deny"


def test_follow_symlinks_never_refuses_links(workspace):
    src = workspace / "target.txt"
    src.write_text("data", encoding="utf-8")
    link = workspace / "alias.txt"
    try:
        os.symlink("target.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    sb = PathSandbox([str(workspace)], SandboxPolicy(follow_symlinks="never"))
    assert code_for(sb, "alias.txt")[1] == "SANDBOX_VIOLATION"


def test_follow_symlinks_within_roots_follows_an_in_root_link(workspace):
    """The default policy, exercised on a host that actually supports links: an
    in-root link is followed, the containment check still runs against the
    *real* target, and the resolution reports what it walked through."""
    target = workspace / "target.txt"
    target.write_text("data", encoding="utf-8")
    link = workspace / "alias.txt"
    try:
        os.symlink("target.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    sb = PathSandbox([str(workspace)])
    verdict, res = code_for(sb, "alias.txt")
    assert verdict == "allow"
    assert res.resolved_via_link is True
    assert res.real == os.path.realpath(str(target))
    # and the via() block names the hop and the final file
    via = res.via()
    assert via["root"] == sb.roots[0]
    assert via["symlink"]["hops"] == [os.path.realpath(str(target))]
    assert via["symlink"]["final"] == os.path.realpath(str(target))
    assert any("symlink" in n for n in via["notes"])


def test_via_reports_root_and_chain_for_multi_hop_links(workspace):
    real = workspace / "real.txt"
    real.write_text("hello\n", encoding="utf-8")
    first, second = workspace / "link1.txt", workspace / "link2.txt"
    try:
        os.symlink("real.txt", first)
        os.symlink("link1.txt", second)  # relative hop, relative hop
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    sb = PathSandbox([str(workspace)])
    _v, res = code_for(sb, "link2.txt")
    via = res.via()
    # each hop is the target as the link wrote it (normalized, relative targets
    # made absolute), and `final` is the fully resolved destination
    hop1 = os.path.normpath(os.path.join(os.path.dirname(str(second)), "link1.txt"))
    assert via["symlink"]["hops"] == [hop1, os.path.realpath(str(real))]
    assert via["symlink"]["final"] == os.path.realpath(str(real))


def test_via_is_plain_for_direct_paths(workspace):
    (workspace / "plain.txt").write_text("x", encoding="utf-8")
    sb = PathSandbox([str(workspace)])
    _v, res = code_for(sb, "plain.txt")
    via = res.via()
    assert via == {"root": sb.roots[0]}, "no hops, no long-path: just the matched root"
    # stat-style to_dict carries the same block
    assert res.to_dict()["via"] == via


def test_deny_and_deny_read_rules(workspace):
    sb = PathSandbox([str(workspace)], SandboxPolicy(deny=["**/*.env"], deny_reads=["**/.env"]))
    assert code_for(sb, ".env", "read")[1] == "DENY_RULE"
    assert code_for(sb, ".env", "write")[1] == "DENY_RULE"
    (workspace / "prod.env").write_text("A=1", encoding="utf-8")
    assert code_for(sb, "prod.env")[1] == "DENY_RULE"
    assert code_for(sb, "README.md")[0] == "allow"


def test_nested_roots_report_most_specific(workspace):
    sub = workspace / "src"
    sb = PathSandbox([str(workspace), str(sub)])
    _v, res = code_for(sb, "src/pkg/mod.py")
    assert res.root == os.path.realpath(str(sub)), "deeper root should win"


def test_write_intent_allows_missing_file_inside_root(workspace):
    sb = PathSandbox([str(workspace)])
    verdict, res = code_for(sb, "new/deep/file.txt", "write")
    assert verdict == "allow" and res.exists is False and res.writable is True


def test_ignore_rules_for_walk(workspace):
    sb = PathSandbox([str(workspace)], SandboxPolicy(ignore=["node_modules/**", "**/*.md"]))
    assert sb.should_ignore("node_modules/junk.js")
    assert sb.should_ignore("README.md")
    assert not sb.should_ignore("src/pkg/mod.py")


def test_safe_helper_does_not_raise(workspace):
    sb = PathSandbox([str(workspace)])
    assert sb.safe("README.md") and not sb.safe("/etc/shadow")


@pytest.mark.parametrize("glob,candidate,expected", [
    ("**/.env", ".env", True),
    ("**/.env", "config/.env", True),
    ("**/.env", ".envrc", False),
    ("**/*.pem", "keys/id.pem", True),
    ("*.txt", "a.txt", True),
    ("*.txt", "dir/a.txt", False),          # single * must not cross separators
    ("src/**/*.py", "src/a/b/c.py", True),
    ("**/node_modules/**", "x/node_modules/y/z.js", True),
])
def test_glob_semantics(glob, candidate, expected):
    assert bool(_glob_to_re(glob).search(candidate)) is expected


def test_case_sensitivity_probe_returns_bool(workspace):
    assert detect_case_sensitivity(str(workspace)) in (True, False)


# --- Windows-shape behaviour we can assert without Windows -------------------

def test_windows_reserved_names_and_ads_are_rejected():
    """These are pure string rules; force the Windows branch to exercise them."""
    from skeletonkey.fsx import sandbox as mod

    orig_win = mod._IS_WIN
    mod._IS_WIN = True
    try:
        sb = PathSandbox(["C:/work"])
        for bad in ["C:/work/CON", "C:/work/nul.txt", "C:/work/data.txt:evil"]:
            assert code_for(sb, bad)[0] == "deny", bad
        assert code_for(sb, "C:/work/ok.txt")[0] == "allow"
    finally:
        mod._IS_WIN = orig_win


def test_msys_path_translation_on_windows_shape():
    from skeletonkey.fsx import sandbox as mod

    orig_win = mod._IS_WIN
    mod._IS_WIN = True
    try:
        sb = PathSandbox(["C:/Users/dime/proj"])
        verdict, res = code_for(sb, "/c/users/dime/proj/notes.md")
        assert verdict == "allow", "git-bash style path should map onto the C: root"
        assert res.abs.replace("\\", "/").lower().endswith("notes.md")
    finally:
        mod._IS_WIN = orig_win


def test_case_insensitive_containment_on_windows_shape():
    from skeletonkey.fsx import sandbox as mod

    orig_win = mod._IS_WIN
    mod._IS_WIN = True
    try:
        sb = PathSandbox(["C:/Users/dime/Proj"])
        assert code_for(sb, "c:\\users\\dime\\proj\\a.txt")[0] == "allow"
        assert code_for(sb, "d:/other/a.txt")[0] == "deny"
    finally:
        mod._IS_WIN = orig_win
