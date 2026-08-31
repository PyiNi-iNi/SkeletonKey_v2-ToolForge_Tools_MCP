"""Journal: what makes a mutation retractable, and when it stops being."""

from __future__ import annotations

import json
import os
import stat
import time
from types import SimpleNamespace

import pytest

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.fsx.journal import FsJournal
from skeletonkey.fsx.sandbox import PathSandbox


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (root / "src" / "big.txt").write_text("x" * 200_000, encoding="utf-8")
    (root / "dir").mkdir()
    (root / "dir" / "one.txt").write_text("one\n", encoding="utf-8")
    (root / "dir" / "two.txt").write_text("two\n", encoding="utf-8")
    return root


def res_for(path) -> SimpleNamespace:
    p = str(path)
    return SimpleNamespace(real=p, display=os.path.basename(p), is_dir=os.path.isdir(p),
                           exists=os.path.exists(p), abs=p)


# ------------------------------------------------------------------ capture
def test_small_change_is_inlined_large_one_is_copied(tree, tmp_path):
    j = FsJournal(str(tmp_path / "state" / "journal"))
    small = j.record_before(res_for(tree / "src" / "a.py"), b"NEW\n", task_id="t1")
    big = j.record_before(res_for(tree / "src" / "big.txt"), b"NEW\n", task_id="t1")
    assert small and big
    rows = {e["token"]: e for e in j.list()}
    # both are on disk (an in-RAM-only before-image would vanish with the process),
    # but only the big one is a copy of the original file
    assert rows[small]["meta"]["stored"] == "staged-inline"
    assert "stored" not in rows[big].get("meta", {})
    assert rows[big]["shadow"].endswith("__big.txt")
    assert os.path.getsize(rows[big]["shadow"]) == 200_000
    assert os.path.getsize(rows[small]["shadow"]) == 11


def test_inline_limit_is_respected_and_reported(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"), inline_limit=4)
    token = j.record_before(res_for(tree / "src" / "a.py"), b"x" * 40, task_id="t")
    row = {e["token"]: e for e in j.list()}[token]
    assert "stored" not in row.get("meta", {}), "over the limit -> copy the file itself"
    assert row["shadow"].endswith("__a.py") and os.path.getsize(row["shadow"]) == 11


def test_missing_source_file_records_a_create(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    token = j.record_new(SimpleNamespace(real=str(tree / "new.txt"), display="new.txt"), task_id="t")
    entry = next(e for e in j.list() if e["token"] == token)
    assert entry["action"] == "create"
    assert "shadow" not in entry, "nothing to restore - the undo deletes the new file"


def test_directory_delete_is_tarred(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    token = j.record_delete(res_for(tree / "dir"), recursive=True, task_id="t")
    entry = next(e for e in j.list() if e["token"] == token)
    assert entry["action"] == "delete"
    archive = os.path.join(j.shadow_dir, f"{token}__tree.tar")
    assert os.path.getsize(archive) > 0
    import tarfile

    with tarfile.open(archive) as tf:
        names = sorted(m.name for m in tf.getmembers() if m.isfile())
    assert any(n.endswith("one.txt") for n in names) and any(n.endswith("two.txt") for n in names)


def test_delete_without_snapshot_is_not_recoverable_but_is_recorded(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"), enabled=False)
    token = j.record_delete(res_for(tree / "dir"), recursive=True, task_id="t")
    assert token == "" , "a disabled journal hands back no token, so callers cannot claim undo"
    assert j.list() == []


# ------------------------------------------------------------------ undo
def test_undo_restores_content_and_is_idempotent(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    target = tree / "src" / "a.py"
    token = j.record_before(res_for(target), b"NEW\n", task_id="t1")
    target.write_text("NEW\n", encoding="utf-8")
    out = j.undo(token)
    assert out["undone"] is True
    assert target.read_text(encoding="utf-8") == "print('a')\n"
    again = j.undo(token)
    assert again["undone"] is False and "already" in again["note"]


def test_undo_of_a_create_removes_the_file(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    created = tree / "src" / "fresh.py"
    token = j.record_new(res_for(created), task_id="t1")
    created.write_text("x = 1\n", encoding="utf-8")
    assert j.undo(token)["undone"] is True
    assert not created.exists()


def test_undo_of_a_create_keeps_a_non_empty_directory(tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    d = tmp_path / "made"
    d.mkdir()
    (d / "inside.txt").write_text("keep me", encoding="utf-8")
    token = j.record_new(SimpleNamespace(real=str(d), display="made", is_dir=True, exists=True,
                                                  abs=str(d)), action="mkdir", task_id="t")
    out = j.undo(token)
    assert d.exists() and (d / "inside.txt").exists(), "undo must never delete files it did not create"
    assert "not empty" in " ".join(out["changes"])


def test_undo_restores_file_mode(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    target = tree / "src" / "a.py"
    os.chmod(target, 0o755)
    token = j.record_before(res_for(target), b"NEW\n", task_id="t")
    os.chmod(target, 0o600)
    j.undo(token)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o755


def test_undo_dry_run_plans_without_touching_disk(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    target = tree / "src" / "a.py"
    token = j.record_before(res_for(target), b"NEW\n", task_id="t")
    target.write_text("NEW\n", encoding="utf-8")
    plan = j.undo(token, dry_run=True)
    assert plan["dry_run"] is True and plan["plan"]["op"] == "restore"
    assert target.read_text() == "NEW\n", "a preview must not be an edit"


def test_undo_task_walks_newest_first_and_stops_on_failure(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    a, b = tree / "src" / "a.py", tree / "src" / "b.py"
    b.write_text("b original\n", encoding="utf-8")
    t1 = j.record_before(res_for(a), b"a v2\n", task_id="bulk")
    a.write_text("a v2\n", encoding="utf-8")
    t2 = j.record_before(res_for(b), b"b v2\n", task_id="bulk")
    b.write_text("b v2\n", encoding="utf-8")

    # someone else edits b mid-task: undo must still undo (that is what was asked)
    # but must say that it rolled over their change
    b.write_text("theirs\n", encoding="utf-8")
    out = j.undo_task("bulk")
    assert out["undone"] == 2 and out["failed"] == []
    assert [r["token"] for r in out["results"]] == [t2, t1], "newest first"
    assert a.read_text(encoding="utf-8") == "print('a')\n"
    assert b.read_text(encoding="utf-8") == "b original\n"
    warned = [r for r in out["results"] if r["token"] == t2]
    assert warned and "changed since" in warned[0]["warnings"][0]



# ------------------------------------------------------------- metadata-only entries
def test_chmod_entry_restores_only_the_mode(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    target = tree / "src" / "a.py"
    os.chmod(target, 0o600)
    token = j.record_meta(res_for(target))
    os.chmod(target, 0o644)
    out = j.undo(token)
    assert out["undone"] is True and out["action"] == "chmod"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text(encoding="utf-8").strip() == "print('a')", "undo must not touch content"


def test_a_zero_mode_survives_the_index_round_trip(tmp_path, tree):
    """`to_dict` drops falsy values, and 0o000 is a mode somebody will genuinely want back."""
    j = FsJournal(str(tmp_path / "j"))
    target = tree / "dir" / "one.txt"
    os.chmod(target, 0o000)
    token = j.record_meta(res_for(target))
    reopened = FsJournal(str(tmp_path / "j"))
    os.chmod(target, 0o644)
    reopened.undo(token)
    assert stat.S_IMODE(target.stat().st_mode) == 0o000


def test_undo_refuses_to_invent_a_mode_when_the_capture_failed(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    gone = tree / "dir" / "vanished.txt"
    token = j.record_meta(res_for(gone))          # the path is unreadable: nothing captured
    row = next(e for e in j.list() if e["token"] == token)
    assert row["meta"]["undo_reliable"] is False
    os.chmod(tree / "src" / "a.py", 0o600)
    plan = j._plan(j._entries[token])
    assert plan["mode"] is None and "cannot restore" in plan["warning"]
    # 0o644 is the dataclass default, and treating "unknown" as that number is how a
    # locked file gets opened by an undo.
    assert stat.S_IMODE((tree / "src" / "a.py").stat().st_mode) == 0o600

def test_undo_unknown_token_reports_known_ones(tree, tmp_path):
    from skeletonkey.core.errors import SkeletonKeyError

    j = FsJournal(str(tmp_path / "j"))
    with pytest.raises(SkeletonKeyError) as exc:
        j.undo("nope")
    assert exc.value.code == "ENOENT"
    assert "advice" in exc.value.details or "known" in exc.value.details


# ------------------------------------------------------------------ durability
def test_index_survives_a_restart(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    token = j.record_before(res_for(tree / "src" / "a.py"), b"NEW\n", task_id="t")
    (tree / "src" / "a.py").write_text("NEW\n", encoding="utf-8")
    j2 = FsJournal(str(tmp_path / "j"))
    assert [e["token"] for e in j2.list()] == [token], "the index replays on restart"
    out = j2.undo(token)
    assert out["undone"] is True, "the before-image must survive the restart, not live in RAM"
    assert (tree / "src" / "a.py").read_text(encoding="utf-8") == "print('a')\n"


def test_torn_index_tail_is_dropped(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    j.record_before(res_for(tree / "src" / "a.py"), b"NEW\n", task_id="t")
    with open(j.index_path, "a", encoding="utf-8") as fh:
        fh.write('{"token":"hal')  # killed mid-write
    j2 = FsJournal(str(tmp_path / "j"))
    assert len(j2.list()) == 1
    token = j2.list()[0]["token"]
    (tree / "src" / "a.py").write_text("NEW\n", encoding="utf-8")
    assert j2.undo(token)["undone"] is True


def test_prune_drops_oldest_first_and_deletes_shadow_files(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"), keep=2)
    tokens = [j.record_before(res_for(tree / "src" / "big.txt"), b"n\n", task_id=f"t{i}")
              for i in range(4)]
    # keep=2 is enforced as entries arrive, not lazily: a long unattended run must
    # not be able to fill the disk with before-images nobody can reach any more.
    live = [e["token"] for e in j.list(limit=99)]
    assert live == tokens[2:][::-1]
    assert len(os.listdir(j.shadow_dir)) == 2, "pruning must reclaim the shadow copies"
    assert j.prune() == 0, "already at the limit"


def test_keep_bounds_entries_at_record_time(tmp_path, tree):
    """A journal nobody prunes becomes the largest directory in the workspace."""
    j = FsJournal(str(tmp_path / "j"), keep=3)
    for _ in range(6):
        j.record_before(res_for(tree / "src" / "a.py"), b"n\n", task_id="t")
    assert len(j.list(limit=99)) == 3
    assert len(os.listdir(j.shadow_dir)) == 3


def test_summary_reports_size_and_actions(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    j.record_before(res_for(tree / "src" / "big.txt"), b"n\n", task_id="t")
    j.record_new(res_for(tree / "src" / "n.py"), task_id="t")
    s = j.summary()
    assert s["entries"] == 2
    assert s["shadow_bytes"] >= 200_000
    assert set(s["by_action"]) <= {"write", "create", "delete", "move"}


def test_list_filters_by_task_and_path(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    j.record_before(res_for(tree / "src" / "a.py"), b"x\n", task_id="one")
    j.record_before(res_for(tree / "src" / "big.txt"), b"x\n", task_id="two")
    assert len(j.list(task_id="one")) == 1
    hits = j.list(paths="big.txt")
    assert len(hits) == 1 and hits[0]["path"] == "big.txt"
    assert j.list(task_id="missing") == []


def test_discard_rolls_back_an_uncommitted_change(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    token = j.record_before(res_for(tree / "src" / "a.py"), b"x\n", task_id="t")
    j.discard(token)
    assert [e["token"] for e in j.list()] != [token]


def test_index_lines_carry_what_undo_needs_and_nothing_else(tmp_path, tree):
    j = FsJournal(str(tmp_path / "j"))
    j.record_before(res_for(tree / "src" / "a.py"), b"x\n", task_id="t")
    j.record_before(res_for(tree / "src" / "a.py"), b"y\n", task_id="t")
    with open(j.index_path, encoding="utf-8") as fh:
        lines = [json.loads(ln) for ln in fh if ln.strip()]
    assert [row["seq"] for row in lines] == [1, 2]
    assert all(row["sha_before"] and row["abs_path"] and row["shadow"] for row in lines)
    assert not any("inline" in row for row in lines), "payloads live in files, not in the index"
    assert all(row["task_id"] == "t" for row in lines)


def test_record_before_tolerates_a_path_that_vanished_mid_call(tmp_path):
    """The file can disappear between resolve and snapshot; that must not lose the write."""
    j = FsJournal(str(tmp_path / "j"))
    ghost = tmp_path / "gone.txt"
    token = j.record_before(SimpleNamespace(real=str(ghost), display="gone.txt"), b"new\n",
                            action="create", task_id="t")
    assert token, "an unrecordable undo is fine, an exception on the write path is not"


def test_undo_warns_when_the_content_is_not_what_we_wrote(tree, tmp_path):
    j = FsJournal(str(tmp_path / "j"))
    target = tree / "src" / "a.py"
    token = j.record_before(res_for(target), b"NEW\n", task_id="t")
    target.write_text("someone else entirely\n", encoding="utf-8")
    os.utime(target, (time.time() - 100, time.time() - 100))
    out = j.undo(token)
    assert out["undone"] is True, "undo still honours the request..."
    assert "changed since" in out["warnings"][0], "...and says it rolled over someone's edit"
    assert target.read_text(encoding="utf-8") == "print('a')\n"


def test_undo_refuses_when_the_target_left_the_roots(tree, tmp_path):
    """Roots get narrowed between a mutation and its undo; the sandbox must re-approve."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "victim.txt").write_text("theirs\n", encoding="utf-8")
    sb = PathSandbox([str(tree)])
    j = FsJournal(str(tmp_path / "j"), sandbox=sb)
    entry = j.record_new(SimpleNamespace(real=str(outside / "victim.txt"), display="victim.txt",
                                         is_dir=False, exists=True, abs=str(outside / "victim.txt")),
                         action="create", task_id="t")
    with pytest.raises(SkeletonKeyError) as exc:
        j.undo(entry)
    assert exc.value.code == E.SANDBOX_VIOLATION.code
    assert (outside / "victim.txt").exists(), "the refusal must not have deleted anything"
    assert "roots" in exc.value.details["advice"]


def test_undo_still_works_for_paths_inside_the_roots(tree, tmp_path):
    sb = PathSandbox([str(tree)])
    j = FsJournal(str(tmp_path / "j"), sandbox=sb)
    target = tree / "src" / "a.py"
    token = j.record_before(res_for(target), b"changed\n", task_id="t")
    target.write_text("changed\n", encoding="utf-8")
    assert j.undo(token)["undone"] is True
    assert target.read_text(encoding="utf-8") == "print('a')\n"
