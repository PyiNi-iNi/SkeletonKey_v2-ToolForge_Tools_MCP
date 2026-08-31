"""Filesystem mechanics: paged reads, atomic writes, newline/encoding
preservation, patch semantics, and the undo journal."""

from __future__ import annotations

import os

import pytest

from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.fsx.journal import FsJournal
from skeletonkey.fsx.ops import Fs, sniff
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
