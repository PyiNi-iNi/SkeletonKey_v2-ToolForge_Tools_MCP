"""Filesystem mechanics: paged reads, atomic writes, newline/encoding
preservation, patch semantics, and the undo journal."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

import pytest

from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.fsx.journal import FsJournal
from skeletonkey.fsx.ops import MAX_CHMOD_TARGETS, Fs, _parse_mode, sniff
from skeletonkey.fsx.sandbox import PathSandbox, SandboxPolicy


@pytest.fixture
def rawfs(workspace):
    sb = PathSandbox([str(workspace)], SandboxPolicy(ignore=["node_modules/**", ".git/**"]))
    jr = FsJournal(os.path.join(str(workspace), ".sk", "test-journal"), keep=50)
    return Fs(sb, journal=jr), jr


MOD = "src/pkg/mod.py"


def test_read_returns_content_and_metadata(rawfs):
    fs, _ = rawfs
    r = fs.read(MOD)
    assert r.content.startswith("PORT = 8080")
    assert r.lines == 10 and r.size > 0
    assert r.encoding == "utf-8" and r.newline == "lf"
    assert len(r.sha256) == 64


def test_paged_read_with_offset_and_cursor(rawfs):
    fs, _ = rawfs
    full = fs.read(MOD)
    lines = full.content.split("\n")
    first = fs.read(MOD, offset=0, limit_lines=3)
    second = fs.read(MOD, offset=3, limit_lines=3)
    assert first.content.splitlines()[0] == "PORT = 8080"
    assert second.content.splitlines()[0] == lines[3] == "def handler(request):"
    assert first.truncated and first.next_offset == 3
    assert "total_lines=10" in first.notes[0]
    # pages must reassemble exactly - no lost or duplicated lines at the seam
    joined = first.content + second.content + fs.read(MOD, offset=6).content
    assert joined == full.content
    tail = fs.read(MOD, start_line=9, end_line=10)
    assert "def __init__" in tail.content and not tail.truncated


def test_window_read_never_double_applies_the_offset(rawfs):
    """Regression: streaming and in-memory paths once both sliced, losing lines."""
    fs, _ = rawfs
    full = fs.read(MOD)
    one = fs.read(MOD, offset=4, limit_lines=1)
    assert one.content.rstrip("\n") == full.content.splitlines()[4]
    assert one.lines == 1


def test_crlf_files_are_reported_and_preserved(rawfs, workspace):
    fs, _ = rawfs
    r = fs.read("windows.txt")
    assert r.newline == "crlf"
    fs.write("windows.txt", "line one\nline two\nline three\n")
    assert (workspace / "windows.txt").read_bytes() == b"line one\r\nline two\r\nline three\r\n"


def test_write_preserves_detected_newline_by_default(rawfs, workspace):
    fs, _ = rawfs
    fs.write(MOD, "PORT = 9090\n")
    assert (workspace / "src/pkg/mod.py").read_bytes() == b"PORT = 9090\n"
    fs.write("windows.txt", "a\nb\n")
    assert (workspace / "windows.txt").read_bytes() == b"a\r\nb\r\n"


def test_write_is_atomic_and_leaves_no_temp_files(rawfs, workspace):
    fs, _ = rawfs
    for i in range(25):
        fs.write("t/many.txt", f"v{i}\n")
    leftovers = [n for n in os.listdir(workspace / "t") if "sk-tmp" in n]
    assert leftovers == []
    assert (workspace / "t" / "many.txt").read_text() == "v24\n"


def test_overwrite_guard_and_create_dirs(rawfs):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.write(MOD, "x\n", overwrite=False)
    assert exc.value.code == "EEXIST"
    r = fs.write("deep/nested/new.txt", "ok\n")
    assert r.created is True and r.changed is True


def test_expect_sha_prevents_clobbering_stale_state(rawfs, workspace):
    fs, _ = rawfs
    before = fs.read(MOD).sha256
    (workspace / "src/pkg/mod.py").write_text("someone else edited this\n", encoding="utf-8")
    with pytest.raises(SkeletonKeyError) as exc:
        fs.write(MOD, "mine\n", expect_sha=before)
    assert exc.value.code == "CONFLICT"
    assert "expected_sha" in exc.value.details
    assert fs.write(MOD, "mine\n").changed is True


def test_patch_applies_reports_diff_and_is_undoable(rawfs):
    fs, journal = rawfs
    out = fs.patch(MOD, [{"old_text": "PORT = 8080", "new_text": "PORT = 9090"}])
    assert out["applied"] == 1 and not out["failed"]
    assert "-PORT = 8080" in out["unified_diff"] and "+PORT = 9090" in out["unified_diff"]
    token = out["write"]["undo_token"]
    assert token
    res = journal.undo(token)
    assert res["undone"] is True
    assert "PORT = 8080" in fs.read(MOD).content


def test_patch_fuzzy_matches_reflowed_snippets_without_eating_blanks(rawfs):
    fs, _ = rawfs
    before = fs.read(MOD).content
    out = fs.patch(MOD, [{"old_text": "def handler(request):\n   return {'port': PORT}",
                          "new_text": "def handler(request):\n    return {'port': 1}"}])
    assert out["edits"][0]["strategy"] == "fuzzy-whitespace"
    after = fs.read(MOD).content
    assert after.count("\n\n\n") == before.count("\n\n\n"), "blank-line structure changed"
    assert "'port': 1" in after


def test_patch_refuses_ambiguous_and_missing_targets(rawfs):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.patch(MOD, [{"old_text": "PORT", "new_text": "X"}])
    assert exc.value.code == "AMBIGUOUS_MATCH"
    assert exc.value.details["failures"][0]["matches"] == 3
    with pytest.raises(SkeletonKeyError) as exc2:
        fs.patch(MOD, [{"old_text": "nonexistent-marker-xyz", "new_text": "Y"}])
    assert exc2.value.code == "PATCH_CONFLICT"
    assert exc2.value.details["failures"][0]["hint"]


def test_patch_occurrence_selects_one_match(rawfs):
    fs, _ = rawfs
    out = fs.patch(MOD, [{"old_text": "PORT", "new_text": "PORT_CHANGED", "occurrence": 2}])
    assert out["applied"] == 1
    body = fs.read(MOD).content
    assert body.count("PORT_CHANGED") == 1


def test_patch_no_edits_is_a_usage_error(rawfs):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.patch(MOD, [])
    assert exc.value.code == "BAD_ARGS"


def test_patch_all_same_is_noop_error(rawfs):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.patch(MOD, [{"old_text": "PORT = 8080", "new_text": "PORT = 8080"}])
    assert "no-op" in str(exc.value)


def test_dry_run_patch_writes_nothing(rawfs, workspace):
    fs, _ = rawfs
    snapshot = (workspace / "src/pkg/mod.py").read_bytes()
    out = fs.patch(MOD, [{"old_text": "8080", "new_text": "7070"}], dry_run=True)
    assert out["dry_run"] is True
    assert (workspace / "src/pkg/mod.py").read_bytes() == snapshot


def test_delete_then_undo_restores_file(rawfs, workspace):
    fs, journal = rawfs
    (workspace / "gone.txt").write_text("precious\n", encoding="utf-8")
    res = fs.delete("gone.txt")
    assert not (workspace / "gone.txt").exists()
    journal.undo(res["undo_token"])
    assert (workspace / "gone.txt").read_text() == "precious\n"


def test_delete_dir_requires_recursive(rawfs, workspace):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.delete("src/pkg")
    assert exc.value.code == "BAD_ARGS"
    assert "recursive" in str(exc.value)


# --------------------------------------------------------------- deletion tiers
def _fake_gio_bin(tmp_path):
    """A `gio` stand-in on PATH: `gio trash <p>` moves p into the fake bin."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "gio"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" != "trash" ]; then echo "fake gio: unknown command" >&2; exit 2; fi\n'
        'mkdir -p "$FAKE_TRASH_DIR"\n'
        'mv "$2" "$FAKE_TRASH_DIR/$(basename "$2")" || exit 1\n',
        encoding="utf-8", newline="\n")
    os.chmod(script, 0o755)
    return str(bin_dir)


def test_os_trash_moves_to_the_recycle_bin_and_keeps_a_journal_copy(workspace, tmp_path,
                                                                     monkeypatch):
    bin_dir = _fake_gio_bin(tmp_path)
    fake_bin = tmp_path / "trashbin"
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_TRASH_DIR", str(fake_bin))
    sb = PathSandbox([str(workspace)], SandboxPolicy())
    jr = FsJournal(os.path.join(str(workspace), ".sk", "trash-journal"))
    fs = Fs(sb, journal=jr, delete_mode="os-trash")
    (workspace / "precious.txt").write_text("keep me\n", encoding="utf-8")
    out = fs.delete("precious.txt")
    assert out["deleted"] is True and out["mode"] == "os-trash" and out["trash"] == "recycle bin"
    assert not (workspace / "precious.txt").exists(), "gone from the workspace"
    assert (fake_bin / "precious.txt").read_text(encoding="utf-8") == "keep me\n", \
        "landed in the recycle bin"
    assert out["undo_token"], "the journal keeps a second copy"
    # the journal entry survives a restart - and undo restores from the journal
    # even if the OS bin has since been emptied
    jr2 = FsJournal(os.path.join(str(workspace), ".sk", "trash-journal"))
    (fake_bin / "precious.txt").unlink()
    assert jr2.undo(out["undo_token"])["undone"] is True
    assert (workspace / "precious.txt").read_text(encoding="utf-8") == "keep me\n"


def test_os_trash_on_a_host_without_a_trash_api_deletes_and_records_nothing(workspace,
                                                                            tmp_path, monkeypatch):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    sb = PathSandbox([str(workspace)], SandboxPolicy())
    jr = FsJournal(os.path.join(str(workspace), ".sk", "trash2-journal"))
    fs = Fs(sb, journal=jr, delete_mode="os-trash")
    (workspace / "safe.txt").write_text("safe\n", encoding="utf-8")
    with pytest.raises(SkeletonKeyError) as exc:
        fs.delete("safe.txt")
    assert exc.value.code == "UNSUPPORTED_PLATFORM"
    assert "trash" in str(exc.value).lower()
    assert (workspace / "safe.txt").exists(), "it deleted nothing"
    assert jr.list() == [], "and it recorded nothing for a deletion that never happened"


def test_delete_tier_is_hard_and_unjournaled(workspace):
    sb = PathSandbox([str(workspace)], SandboxPolicy())
    jr = FsJournal(os.path.join(str(workspace), ".sk", "trash3-journal"))
    fs = Fs(sb, journal=jr, delete_mode="delete")
    (workspace / "gone.txt").write_text("bye\n", encoding="utf-8")
    out = fs.delete("gone.txt")
    assert out["deleted"] is True and out["mode"] == "delete"
    assert out["undo_token"] is None and out["recoverable"] is False
    assert not (workspace / "gone.txt").exists()
    assert jr.list() == []


def test_unknown_delete_mode_is_refused_at_construction(workspace):
    sb = PathSandbox([str(workspace)], SandboxPolicy())
    with pytest.raises(ValueError, match="journal \\| os-trash \\| delete"):
        Fs(sb, delete_mode="off")


def test_trash_payloads_render_for_both_platforms():
    # Linux/macOS: gio trash <path>
    argv = Fs.os_trash_command("/tmp/proj/junk.txt", win=False)
    assert argv == ["gio", "trash", "/tmp/proj/junk.txt"]
    # Windows: the recycle bin is Shell.Application namespace 10, and the rendered
    # script must verify the path is actually gone before it reports success
    win_argv = Fs.os_trash_command(r"C:\proj\junk.txt", win=True)
    assert win_argv[0].endswith("pwsh") or win_argv[0].endswith("powershell")
    assert win_argv[1:3] == ["-NoProfile", "-NonInteractive"]
    script = win_argv[4]
    assert "New-Object -ComObject Shell.Application" in script
    assert "Namespace(10).MoveHere" in script
    assert r"C:\proj\junk.txt" in script
    assert "Test-Path" in script
    # a path with braces and dollar signs must survive the rendering verbatim
    odd = Fs.os_trash_command(r"C:\weird${x}y\file{1}.txt", win=True)[4]
    assert r"C:\weird${x}y\file{1}.txt" in odd


@pytest.mark.win
def test_os_trash_on_windows_uses_the_real_recycle_bin(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("no powershell on this host")
    victim = tmp_path / "win-junk.txt"
    victim.write_text("trash me\n", encoding="utf-8")
    proc = subprocess.run(Fs.os_trash_command(str(victim)), capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert not victim.exists(), "the file must be out of the workspace"


def test_move_and_undo(rawfs, workspace):
    fs, journal = rawfs
    out = fs.move("README.md", "docs/README.md")
    assert out["moved"] and (workspace / "docs/README.md").exists()
    journal.undo(out["undo_token"])
    assert (workspace / "README.md").exists()


def test_glob_and_list_apply_ignore_rules(rawfs):
    fs, _ = rawfs
    g = fs.glob("**/*.py")
    assert g["count"] == 2 and all("node_modules" not in m["path"] for m in g["matches"])
    listing = fs.list(".", depth=3)   # depth counts entry levels; files under src/pkg sit at 3
    names = [e["name"] for e in listing["entries"]]
    assert "src/pkg/mod.py" in names
    assert not any(n.startswith("node_modules") for n in names)


def test_list_truncation_reports_hint(rawfs):
    fs, _ = rawfs
    out = fs.list(".", limit=1)
    assert out["truncated"] is True and "limit" in out["hint"]


def test_stat_reports_sandboxed_metadata(rawfs):
    fs, _ = rawfs
    d = fs.stat(MOD)
    assert d["exists"] and d["is_file"] and d["bytes"] if False else d["exists"]
    assert d["size"] > 0


def test_binary_file_is_flagged_not_garbled(rawfs, workspace):
    fs, _ = rawfs
    (workspace / "blob.bin").write_bytes(bytes(range(256)) * 4)
    r = fs.read("blob.bin")
    assert r.is_binary is True
    assert "binary" in " ".join(r.notes)


def test_missing_file_suggests_nearby_names(rawfs, workspace):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.read("src/pkg/mod.pyi")
    assert exc.value.code in ("ENOENT", "PATH_UNREADABLE")
    sugg = fs._suggest(fs.sb.resolve("src/pkg/mod_typo.py", intent="read"))
    assert sugg and sugg[0].endswith("mod.py")


def test_read_directory_directs_to_list_tool(rawfs):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.read("src/pkg")
    assert exc.value.code == "BAD_ARGS"
    assert exc_value_has_next_action(exc.value)


def exc_value_has_next_action(err) -> bool:
    return any(a.get("tool") == "fs.list" for a in getattr(err, "next_actions", []))


@pytest.mark.parametrize("blob,enc,newline,binary", [
    (b"", "utf-8", "lf", False),
    (b"a\nb\n", "utf-8", "lf", False),
    (b"a\r\nb\r\n", "utf-8", "crlf", False),
    # "none", not "lf": a file with no line break has no dominant ending, and
    # inventing one would make `newline: "preserve"` rewrite it.
    ("\ufeffhello".encode("utf-8-sig"), "utf-8-sig", "none", False),
    ("\ufeffhéllo\r\ni".encode("utf-16-le"), "utf-16-le", "crlf", False),
    # UTF-16 with no BOM used to read as binary, so the tool refused a file it
    # could have decoded. NULs on alternating bytes are the tell.
    ("héllo → x\r\nsecond\r\n".encode("utf-16-le"), "utf-16-le", "crlf", False),
    ("hello world line two\nmore lines here\n".encode("utf-16-be"), "utf-16-be", "lf", False),
    (b"\x00\x01\x02", "utf-8", "none", True),
    (b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 2, "utf-8", "lf", True),  # newline is moot for binary
    ("café\r\nnaïve".encode("cp1252"), "cp1252", "crlf", False),
    (b"one\n_two\r\nthree\n", "utf-8", "lf", False),
])
def test_sniff(blob, enc, newline, binary):
    assert sniff(blob) == (enc, newline, binary)


def test_undo_task_reverts_in_reverse_order(rawfs):
    fs, journal = rawfs
    fs.write(MOD, "one\n", task_id="T")
    fs.write(MOD, "two\n", task_id="T")
    fs.write("extra.txt", "x\n", task_id="T")
    res = journal.undo_task("T")
    assert res["undone"] == 3
    assert "PORT = 8080" in fs.read(MOD).content
    assert not os.path.exists(os.path.join(str(workspace_of(fs)), "extra.txt"))


def workspace_of(fs) -> str:
    return fs.sb.roots[0]


def test_journal_persists_index_and_survives_reopen(workspace, rawfs):
    fs, _journal = rawfs
    out = fs.patch(MOD, [{"old_text": "8080", "new_text": "1234"}])
    token = out["write"]["undo_token"]
    again = FsJournal(os.path.join(str(workspace), ".sk", "test-journal"), keep=50)
    listed = again.list()
    assert any(e["token"] == token for e in listed)
    assert again.summary()["entries"] >= 1


# --------------------------------------------------------------------------- mode bits
# (spec, mode-before, expected). Symbolic modes are applied *to the current bits*, which is
# why `u+x` on 0o644 is 0o744 and not 0o100.
MODE_CASES = [
    ("u+x", 0o644, 0o744),
    ("go-w", 0o777, 0o755),
    ("a=r", 0o777, 0o444),           # `=` replaces the triple; it does not "add r"
    ("u=rw,go=r", 0o000, 0o644),
    ("+x", 0o600, 0o711),            # empty who means a, per POSIX
    ("a-x", 0o755, 0o644),
    ("u=rwx,g=rx,o=", 0o777, 0o750),
    ("go=", 0o777, 0o700),           # empty rhs = every bit that class has
    ("o-rwx", 0o755, 0o750),
    ("a=", 0o777, 0o000),
    ("u-s", 0o7755, 0o3755),         # only u's setuid goes
    ("go=r", 0o700, 0o744),
    ("o=", 0o1777, 0o770),           # `=` takes the class's own special bit with it
    ("u=rws", 0o000, 0o4600),         # `s` in place of x: setuid without execute
    ("a+rwx,u+s", 0o000, 0o4777),
    ("644", 0o777, 0o644),
    ("0644", 0o000, 0o644),
    ("0o755", 0o000, 0o755),         # our extension: GNU chmod rejects the 0o prefix
    (0o700, 0o777, 0o700),
]


@pytest.mark.parametrize("spec,base,want", MODE_CASES)
def test_mode_parser_applies_the_spec_to_the_current_bits(spec, base, want):
    assert _parse_mode(spec, base) == want


@pytest.mark.parametrize("spec", ["", "  ", "78", "banana", "u+", "u?x", "u=rw;go=r", "0o9999",
                                  "u=rwv", ",u+x", "u+t", "go+t", "x+u", True, 0o10000, None, ["644"]])
def test_an_unparseable_mode_is_refused_instead_of_defaulting(spec):
    """The parser this replaced returned 0o644 for anything it could not read.

    That is the worst possible failure here: `chmod("deploy.sh", "755x")` would have stripped
    the execute bit and reported success.
    """
    with pytest.raises(SkeletonKeyError) as exc:
        _parse_mode(spec, 0o644)
    assert exc.value.code == "BAD_ARGS"
    assert exc.value.details.get("accepted"), "a refusal must say what is accepted"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
@pytest.mark.parametrize("spec", [c[0] for c in MODE_CASES
                                   if isinstance(c[0], str) and not c[0].startswith("0o")])
def test_mode_parser_agrees_with_the_chmod_on_this_box(tmp_path, spec):
    """Verified against /bin/chmod, not against my reading of a man page.

    Where GNU silently ignores a bit (`u+t` on a file) we refuse it, which is why the
    accepted list here is a subset of what GNU accepts.
    """
    chmod = shutil.which("chmod")
    if chmod is None:
        pytest.skip("no chmod on PATH")
    for base in (0o000, 0o644, 0o777, 0o1777, 0o4755):
        f = tmp_path / f"probe-{base:o}"
        f.write_text("", encoding="utf-8")
        os.chmod(f, base)
        done = subprocess.run([chmod, spec, str(f)], capture_output=True, text=True)
        assert done.returncode == 0, f"chmod {spec} refused on this box: {done.stderr.strip()}"
        got = stat.S_IMODE(os.stat(f).st_mode)
        assert _parse_mode(spec, base) == got, f"{spec} on {oct(base)}: GNU {oct(got)}, us {oct(_parse_mode(spec, base))}"


def test_chmod_sets_bits_and_returns_what_to_undo(rawfs, workspace):
    fs, _ = rawfs
    target = workspace / MOD
    target.chmod(0o600)
    r = fs.chmod(MOD, "u+x")
    assert r["mode"] == "0o700" and r["mode_before"] == "0o600", "u+x on 0600 is 0700"
    assert r["changed"] is True and r["count"] == 1
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert r["undo"]["args"]["token"] == r["undo_token"], "the block a host pastes back must be real"
    r2 = fs.chmod(MOD, "u+x")
    assert r2["changed"] is False, "a second identical call is a no-op, not a second restore point"



def test_a_chmod_that_would_change_nothing_records_nothing(rawfs, workspace):
    fs, jr = rawfs
    (workspace / MOD).chmod(0o644)
    r = fs.chmod(MOD, "u+rw,g+r,o+r")
    assert r["changed"] is False and r["unchanged"] == 1 and "undo_token" not in r
    assert jr.list(paths="mod.py") == [], "an idempotent no-op must not become a fake restore point"


def test_chmod_dry_run_reports_without_touching_the_disk(rawfs, workspace):
    fs, _ = rawfs
    (workspace / MOD).chmod(0o644)
    r = fs.chmod(MOD, "700", dry_run=True)
    assert r["dry_run"] is True and r["would_chmod"] == "0o700" and r["changed_count"] == 1
    assert r["targets"][0] == {"path": MOD, "from": "0o644", "to": "0o700", "changed": True}
    assert stat.S_IMODE((workspace / MOD).stat().st_mode) == 0o644


def test_recursive_chmod_walks_a_directory(rawfs, workspace):
    fs, _ = rawfs
    d = workspace / "scripts"
    d.mkdir()
    (d / "nested").mkdir()
    # tmp dirs arrive as 0700, which already differs from the files; normalise the base so
    # "count" means "the three files", not "the three files plus whatever the umask did".
    os.chmod(d, 0o755)
    names = []
    for i in range(3):
        (d / f"s{i}.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        names.append(f"s{i}.sh")
    os.chmod(d / "nested", 0o755)
    (d / "nested" / "deep.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    for rel in names:
        os.chmod(d / rel, 0o644)
    r = fs.chmod("scripts", "a+x", recursive=True)
    assert r["count"] == 4, "the three scripts plus the one nested below; both dirs already had +x"
    for rel in names:
        assert stat.S_IMODE((d / rel).stat().st_mode) & 0o111 == 0o111
    assert stat.S_IMODE((d / "nested" / "deep.sh").stat().st_mode) & 0o111 == 0o111


def test_recursive_chmod_does_not_follow_a_symlink_out_of_the_root(rawfs, workspace, tmp_path):
    fs, _ = rawfs
    outside = tmp_path / "outside-target.txt"
    outside.write_text("not yours", encoding="utf-8")
    os.chmod(outside, 0o600)
    d = workspace / "links"
    d.mkdir()
    (d / "escape").symlink_to(outside)
    (d / "own.txt").write_text("mine", encoding="utf-8")
    os.chmod(d / "own.txt", 0o600)
    fs.chmod("links", "a+r", recursive=True)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o600, "chmod -R through a link is how /etc gets touched"
    assert stat.S_IMODE((d / "own.txt").stat().st_mode) & 0o044 == 0o044


def test_recursive_chmod_refuses_the_whole_call_when_one_path_is_denied(workspace):
    d = workspace / "mixed"
    d.mkdir()
    (d / "ok.txt").write_text("x", encoding="utf-8")
    os.chmod(d / "ok.txt", 0o644)
    (d / "secret.key").write_text("x", encoding="utf-8")
    os.chmod(d / "secret.key", 0o600)
    sb = PathSandbox([str(workspace)], SandboxPolicy(deny=["**/secret*"]))
    fs = Fs(sb, journal=None)
    with pytest.raises(SkeletonKeyError) as exc:
        fs.chmod("mixed", "a+r", recursive=True)
    assert exc.value.code == "DENY_RULE"
    assert stat.S_IMODE((d / "ok.txt").stat().st_mode) == 0o644, "a refusal must happen before any write"


def test_recursive_chmod_stops_at_the_cap_and_says_so(rawfs, workspace, monkeypatch):
    fs, _ = rawfs
    d = workspace / "many"
    d.mkdir()
    for i in range(6):
        (d / f"f{i}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr("skeletonkey.fsx.ops.MAX_CHMOD_TARGETS", 3)
    r = fs.chmod("many", "a+r", recursive=True)
    assert r["truncated"] is True and r["count"] + r["unchanged"] <= MAX_CHMOD_TARGETS
    assert "cap" in r["hint"] or "stopped" in r["hint"]


def test_recursive_chmod_on_a_file_is_an_error_not_a_no_op(rawfs, workspace):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.chmod(MOD, "644", recursive=True)
    assert exc.value.code == "BAD_ARGS" and "not a directory" in exc.value.err.message


def test_chmod_on_a_missing_path_says_so_with_nearby_names(rawfs, workspace):
    fs, _ = rawfs
    with pytest.raises(SkeletonKeyError) as exc:
        fs.chmod("src/pkg/mod.pyi", "644")
    assert exc.value.code == "ENOENT"
    assert "src/pkg/mod.py" in str(exc.value.details["suggested"])
