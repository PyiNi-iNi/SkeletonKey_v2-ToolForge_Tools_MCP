"""Undo journal: shadow copies of only the paths a task touched.

Full snapshots are too slow and too big for an unattended loop; a
per-mutation shadow copy is cheap and enough to reverse exactly what the agent
did. Small files are stored inline in the index (base64, size-capped) and large
ones as shadow copies, so `undo` works for a 4000-line refactor without
duplicating a monorepo.

Deletions are recoverable too (we copy before unlink), and directories are
tarred. `undo_task` replays in reverse order, which is what "revert the last
turn" means in practice.
"""

from __future__ import annotations

import base64
import json as _json
import os
import shutil
import tarfile
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import E, SkeletonKeyError
from ..core.util import compact_json, new_run_id, short_hash

INLINE_LIMIT = 96 * 1024


@dataclass
class JournalEntry:
    token: str
    seq: int
    ts: float
    action: str                 # write | create | delete | move | chmod
    path: str                   # display path
    abs_path: str
    task_id: str = ""
    shadow: str | None = None   # file/dir copy on disk
    inline: str | None = None   # base64 of previous content (small files)
    sha_before: str | None = None
    sha_after: str | None = None      # what we were about to write: lets undo detect a clobber
    existed_before: bool = True
    bytes_before: int = 0
    mode: int = 0o644
    mtime: float = 0.0
    moved_to: str | None = None
    restored: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, False, "", 0) and k != "inline"}
        if self.inline:
            d["inline_bytes"] = len(base64.b64decode(self.inline))
        return d


class FsJournal:
    def __init__(self, root: str, *, enabled: bool = True, keep: int = 200,
                 sandbox: Any = None, inline_limit: int = INLINE_LIMIT) -> None:
        self.root = os.path.abspath(root)
        self.enabled = enabled
        self.keep = keep
        self.sandbox = sandbox
        self.inline_limit = inline_limit
        self.index_path = os.path.join(self.root, "index.ndjson")
        self.shadow_dir = os.path.join(self.root, "shadow")
        self._entries: dict[str, JournalEntry] = {}
        self._order: list[str] = []
        self._seq = 0
        if self.enabled:
            os.makedirs(self.shadow_dir, exist_ok=True)
            self._load_index()

    # -------------------------------------------------------------------- record
    def record_before(self, res: Any, upcoming_bytes: bytes, *, action: str = "write",
                      task_id: str = "") -> str:
        entry = self._new_entry(action, res, task_id)
        entry.sha_after = _sha_bytes(upcoming_bytes)
        path = res.real
        try:
            size = os.path.getsize(path)
            entry.bytes_before = size
            entry.sha_before = _sha_file(path)
            entry.mode = _mode(path)
            entry.mtime = _mtime(path)
            if size <= self.inline_limit:
                with open(path, "rb") as fh:
                    entry.inline = base64.b64encode(fh.read()).decode("ascii")
            else:
                entry.shadow = self._copy_shadow(path, entry.token)
        except OSError as exc:
            entry.meta["capture_error"] = str(exc)
            entry.meta["undo_reliable"] = False
        return self._commit(entry)

    def record_new(self, res: Any, *, action: str = "create", task_id: str = "") -> str:
        entry = self._new_entry(action, res, task_id)
        entry.existed_before = False
        entry.shadow = None
        entry.inline = None
        return self._commit(entry)

    def record_meta(self, res, *, action: str = "chmod", task_id: str = "") -> str:
        """Journal a metadata-only change: no content moved, so there is no before-image to
        keep - the previous mode *is* the before-image.

        `entry.mode` alone is not enough. The index serialiser drops falsy values and `0o000`
        is a perfectly legitimate mode to undo back to, so the number is mirrored in `meta`,
        where a zero survives.
        """
        entry = self._new_entry(action, res, task_id)
        entry.existed_before = True
        try:
            bits = _stat_mode(res.real)
        except OSError as exc:
            # Never invent a mode to restore: an undo that chmods 0o644 because the stat
            # failed would hand read+write to something that was deliberately locked.
            entry.meta["capture_error"] = str(exc)
            entry.meta["undo_reliable"] = False
        else:
            entry.mode = bits
            entry.mtime = _mtime(res.real)
            entry.meta["mode_before"] = bits
        return self._commit(entry)

    def record_delete(self, res: Any, *, recursive: bool = False, task_id: str = "") -> str:
        entry = self._new_entry("delete", res, task_id)
        entry.existed_before = True
        path = res.real
        try:
            if res.is_dir:
                entry.shadow = self._tar_shadow(path, entry.token)
            elif os.path.getsize(path) <= self.inline_limit:
                with open(path, "rb") as fh:
                    entry.inline = base64.b64encode(fh.read()).decode("ascii")
                entry.bytes_before = os.path.getsize(path)
            else:
                entry.shadow = self._copy_shadow(path, entry.token)
            entry.mode = _mode(path)
            entry.sha_before = _sha_file(path) if not res.is_dir else None
        except OSError as exc:
            entry.meta["capture_error"] = str(exc)
            entry.meta["undo_reliable"] = False
        entry.meta["recursive"] = recursive
        return self._commit(entry)

    def record_move(self, src: Any, dst: Any, *, task_id: str = "") -> str:
        entry = self._new_entry("move", src, task_id)
        entry.moved_to = dst.abs
        entry.meta["dst_display"] = dst.display
        if dst.exists:
            entry.meta["dst_shadow"] = self._copy_shadow(dst.real, entry.token + "-dst")
            entry.meta["dst_existed"] = True
        return self._commit(entry)

    def discard(self, token: str | None) -> None:
        if not token:
            return
        entry = self._entries.pop(token, None)
        if entry and entry.token in self._order:
            self._order.remove(entry.token)
        if entry and entry.shadow:
            _rm(entry.shadow)

    # ---------------------------------------------------------------------- undo
    def _refuse_if_disabled(self) -> None:
        if self.enabled:
            return
        # "unknown token" would send an agent re-reading its own transcript; the truth is
        # that nothing was ever recorded, and only a config change can fix that.
        raise SkeletonKeyError(
            E.NOT_IMPLEMENTED, "the change journal is disabled, so nothing can be undone",
            details={"config": "state.journal", "state_dir": self.root,
                     "advice": "set state.journal = true before mutating, or restore from VCS"},
            next_actions=[{"tool": "fs.journal_list", "args": {}, "note": "confirms enabled: false"}],
        )

    def undo(self, token: str, *, dry_run: bool = False) -> dict[str, Any]:
        self._refuse_if_disabled()
        entry = self._entries.get(token)
        if entry is None:
            raise SkeletonKeyError(
                E.ENOENT, f"unknown undo token {token!r}",
                details={"token": token, "known": self._order[-10:][::-1], "total": len(self._order),
                         "advice": "undo tokens come from write/patch/delete results"},
            )
        if entry.restored:
            return {"token": token, "undone": False, "note": "already undone", "action": entry.action}
        plan = self._plan(entry)
        if dry_run:
            return {"token": token, "dry_run": True, "plan": plan, "action": entry.action,
                    "path": entry.path, "reliable": entry.meta.get("undo_reliable", True)}
        clobber = _content_diverged(entry)
        done = self._apply(plan)
        entry.restored = True
        self._rewrite_index()
        warnings: list[str] = []
        if plan.get("warning"):
            warnings.append(plan["warning"])
        if clobber:
            warnings.append("content had changed since this entry wrote it; the before-image was "
                            "restored over that edit. If someone else changed the file, re-apply "
                            "their change now.")
        return {"token": token, "undone": True, "action": entry.action, "path": entry.path,
                "changes": done, **({"warnings": warnings} if warnings else {})}

    def undo_task(self, task_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        self._refuse_if_disabled()
        targets = [t for t in reversed(self._order)
                   if self._entries[t].task_id == task_id and not self._entries[t].restored]
        if not targets:
            return {"task_id": task_id, "undone": 0, **({"dry_run": True} if dry_run else {}),
                    "note": "nothing journaled for this task",
                    "hint": "undo tokens are per-mutation; check fs.journal_list"}
        results, failures = [], []
        for token in targets:
            try:
                results.append(self.undo(token, dry_run=dry_run))
            except SkeletonKeyError as exc:
                failures.append({"token": token, "code": exc.code, "message": str(exc)})
        return {"task_id": task_id, "undone": len(results), "failed": failures, "results": results,
                **({"dry_run": True} if dry_run else {}),
                **({} if dry_run else {"note": "reverse order applied; later edits to the same file win"})}

    def _plan(self, entry: JournalEntry) -> dict[str, Any]:
        """What restoring this entry means, without touching the disk yet."""
        plan: dict[str, Any] = {"target": entry.abs_path, "action": entry.action,
                                "display": entry.path, "entry": entry}
        if entry.action in ("create", "mkdir"):
            plan["op"] = "delete"
            plan["recursive"] = os.path.isdir(entry.abs_path)
            return plan
        if entry.action == "delete":
            plan["op"] = "restore"
            plan["from"] = entry.shadow or "inline"
            plan["mkdirs"] = True
            return plan
        if entry.action == "chmod":
            plan["op"] = "chmod"
            # `entry.mode` is 0o644 by default, and "the previous mode is unknown" is not the
            # same fact as "the previous mode was 0o644" - so an unreliable capture yields no
            # mode at all rather than a plausible-looking one that silently opens a locked file.
            captured = entry.meta.get("mode_before") if entry.meta.get("undo_reliable", True) else None
            plan["mode"] = captured
            if captured is None:
                plan["warning"] = ("the previous mode could not be read when this was recorded, so "
                                   "undo cannot restore it - set it explicitly with fs.chmod")
            return plan
        if entry.action == "move":
            plan["op"] = "move-back"
            plan["from"] = entry.moved_to
            if entry.meta.get("dst_existed"):
                plan["restore_dst"] = entry.meta.get("dst_shadow")
            return plan
        plan["op"] = "restore"
        plan["from"] = entry.shadow or "inline"
        plan["mkdirs"] = True
        if not entry.existed_before:
            plan["warning"] = "no before-image captured; restore will overwrite current content"
        return plan

    def _approve(self, path: str, intent: str, plan: dict[str, Any]) -> None:
        """The sandbox must approve the undo *now*, not vouch for the roots as they
        were when the change was recorded: roots get narrowed, and an index can be
        replayed in another checkout."""
        if self.sandbox is None or not path:
            return
        try:
            self.sandbox.resolve(path, intent=intent)
        except SkeletonKeyError as exc:
            raise SkeletonKeyError(
                E.SANDBOX_VIOLATION,
                f"undo refused: {intent} target {plan.get('display', path)} is outside the "
                f"current roots",
                details={"path": path, "entry": plan.get("action"), "sandbox": exc.err.message,
                         "advice": "widen roots back to what they were, or restore from VCS"},
            ) from None

    def _apply(self, plan: dict[str, Any]) -> list[str]:
        op = plan["op"]
        target = plan["target"]
        self._approve(target, "write", plan)
        done: list[str] = []
        if op == "delete":
            if os.path.isdir(target):
                # Only remove what we might have created: a directory that now has
                # content was populated by someone else, and rmtree would be a
                # data-destroying surprise on an "undo" - the least destructive verb.
                if plan.get("recursive") and not os.listdir(target):
                    os.rmdir(target)
                    done.append(f"removed empty dir {plan['display']}")
                elif os.listdir(target):
                    done.append(f"left {plan['display']} in place: not empty, "
                                "so it holds files this undo did not create")
                else:
                    os.rmdir(target)
                    done.append(f"removed empty dir {plan['display']}")
            elif os.path.exists(target):
                os.unlink(target)
                done.append(f"removed {plan['display']}")
            else:
                done.append("nothing to remove")
            return done
        if op == "chmod":
            mode = plan.get("mode")
            if mode is None:
                raise SkeletonKeyError(
                    E.CONFLICT, "this entry records no mode to restore",
                    details={"path": plan["display"], "token": getattr(plan.get("entry"), "token", ""),
                             "advice": "set the mode explicitly with fs.chmod instead of undoing"},
                )
            if not os.path.exists(target):
                return [f"mode not restored: {plan['display']} no longer exists"]
            try:
                os.chmod(target, mode)
            except OSError as exc:
                raise SkeletonKeyError(
                    E.UNSUPPORTED_PLATFORM, f"could not restore the mode on {plan['display']}: {exc}",
                    details={"path": plan["display"], "mode": oct(mode)}) from exc
            return [f"restored mode {oct(mode)} on {plan['display']}"]

        if op == "move-back":
            src = plan["from"]
            # moving it back also takes it away, so the current location must be in scope too
            self._approve(src, "write", plan)
            if os.path.exists(src):
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                shutil.move(src, target)
                done.append(f"moved {os.path.basename(src)} back to {plan['display']}")
            else:
                done.append(f"source {src} no longer present")
            if plan.get("restore_dst") and os.path.exists(plan["restore_dst"]):
                shutil.copy2(plan["restore_dst"], src)
                done.append(f"restored overwritten destination {src}")
            return done
        # restore (the before-image lives in the journal's own shadow dir, which is
        # deliberately outside the sandbox - only the destination needs approving)
        parent = os.path.dirname(target) or "."
        if plan.get("mkdirs") and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        entry = plan["entry"]
        if plan["from"] == "inline":
            if not entry.inline:
                raise SkeletonKeyError(E.CONFLICT, "before-image was lost (journal restarted mid-task)",
                                       details={"path": plan["display"], "token": entry.token,
                                                "advice": "restore from your own backup or from VCS"})
            with open(target, "wb") as fh:
                fh.write(base64.b64decode(entry.inline))
            done.append(f"restored {plan['display']} from inline before-image")
        else:
            shadow = plan["from"]
            if shadow.endswith(".tar"):
                with tarfile.open(shadow) as tf:
                    tf.extractall(os.path.dirname(target), filter="data")
                done.append(f"re-extracted directory {plan['display']}")
            else:
                shutil.copy2(shadow, target)
                done.append(f"restored {plan['display']} from shadow copy")
        if os.path.exists(target):
            # Restoring content but silently clearing the exec bit is how an undo
            # breaks a script the caller never touched.
            if entry.mode:
                try:
                    os.chmod(target, entry.mode)
                except OSError:
                    pass
            if entry.mtime:
                try:
                    os.utime(target, (entry.mtime, entry.mtime))
                except OSError:
                    pass
        return done

    # ------------------------------------------------------------------- listing
    def list(self, *, task_id: str | None = None, limit: int = 50, paths: str | None = None) -> list[dict[str, Any]]:
        items = [self._entries[t] for t in reversed(self._order)]
        if task_id:
            items = [e for e in items if e.task_id == task_id]
        if paths:
            needle = paths.lower()
            items = [e for e in items if needle in e.path.lower()]
        return [e.to_dict() for e in items[:limit]]

    def summary(self) -> dict[str, Any]:
        shadow_bytes = 0
        for dirpath, _d, files in os.walk(self.shadow_dir):
            for f in files:
                try:
                    shadow_bytes += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        by_action: dict[str, int] = {}
        for e in self._entries.values():
            by_action[e.action] = by_action.get(e.action, 0) + 1
        return {"entries": len(self._order), "by_action": by_action, "shadow_bytes": shadow_bytes,
                "root": self.root, "enabled": self.enabled, "index": self.index_path}

    def prune(self) -> int:
        """Drop the oldest entries past `keep`, plus their shadow copies."""
        over = len(self._order) - self.keep
        if over <= 0:
            return 0
        removed = 0
        for token in self._order[:over]:
            entry = self._entries.pop(token, None)
            if entry and entry.shadow:
                _rm(entry.shadow)
            removed += 1
        self._order = self._order[over:]
        self._rewrite_index()
        return removed

    # ----------------------------------------------------------------- internals
    def _new_entry(self, action: str, res: Any, task_id: str) -> JournalEntry:
        self._seq += 1
        entry = JournalEntry(token=f"und_{short_hash(new_run_id(), 12)}", seq=self._seq, ts=time.time(),
                             action=action, path=res.display, abs_path=res.real, task_id=task_id)
        return entry

    def _commit(self, entry: JournalEntry) -> str:
        if not self.enabled:
            return ""
        # An inline before-image that lives only in RAM is not a journal: the whole
        # point is surviving the crash that makes you want the undo. Spill it to the
        # shadow dir (raw bytes, no base64) and keep the index line small.
        if entry.inline and not entry.shadow:
            try:
                entry.shadow = self._stage_inline(entry)
                entry.meta["stored"] = "staged-inline"
            except OSError as exc:
                entry.meta["stage_error"] = str(exc)
        self._entries[entry.token] = entry
        self._order.append(entry.token)
        try:
            with open(self.index_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(compact_json(entry.to_dict()) + "\n")
        except OSError:
            entry.meta["index_write_failed"] = True
        if len(self._order) > self.keep:
            try:
                self.prune()
            except OSError:
                pass
        return entry.token

    def _stage_inline(self, entry: JournalEntry) -> str:
        dst = os.path.join(self.shadow_dir, f"{entry.token}__staged")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(base64.b64decode(entry.inline))
        return dst

    def _copy_shadow(self, path: str, token: str) -> str:
        dst = os.path.join(self.shadow_dir, f"{token}__{os.path.basename(path)}")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(path, dst)
        return dst

    def _tar_shadow(self, path: str, token: str) -> str:
        dst = os.path.join(self.shadow_dir, f"{token}__tree.tar")
        with tarfile.open(dst, "w") as tf:
            tf.add(path, arcname=os.path.basename(path))
        return dst

    def _load_index(self) -> None:
        if not os.path.exists(self.index_path):
            return
        try:
            with open(self.index_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        obj = _json.loads(line)
                    except ValueError:
                        continue
                    # inline payloads are not persisted; shadow refs are
                    entry = JournalEntry(**{k: v for k, v in obj.items()
                                            if k in JournalEntry.__dataclass_fields__ and k != "inline"})
                    if entry.inline:
                        entry.meta["inline_lost_on_restart"] = True
                        entry.inline = None
                    self._entries[entry.token] = entry
                    self._order.append(entry.token)
                    self._seq = max(self._seq, entry.seq)
        except OSError:
            pass

    def _rewrite_index(self) -> None:
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for token in self._order:
                fh.write(_json.dumps(self._entries[token].to_dict(), separators=(",", ":")) + "\n")
        os.replace(tmp, self.index_path)


def _sha_file(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data or b"").hexdigest()


def _content_diverged(entry: JournalEntry) -> bool:
    """True when the file no longer holds what this entry put there.

    Undo still runs - "undo" has to mean something - but the caller is told, because
    restoring a before-image over someone else's newer edit is a real conflict and
    silently winning it is the worst possible behaviour for a shared workspace.
    """
    if not entry.sha_after or not os.path.isfile(entry.abs_path):
        return False
    try:
        return _sha_file(entry.abs_path) != entry.sha_after
    except OSError:
        return False


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _mode(path: str) -> int:
    try:
        return _stat_mode(path)
    except OSError:
        return 0o644


def _stat_mode(path: str) -> int:
    import stat as _stat

    return _stat.S_IMODE(os.stat(path).st_mode)


def _rm(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass
