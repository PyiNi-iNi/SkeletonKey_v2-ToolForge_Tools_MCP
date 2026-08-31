"""Filesystem operations, all routed through the PathSandbox.

Principles that matter for an agent loop:
  * writes are **atomic** (tmp + flush + fsync + os.replace) so a killed agent
    cannot leave a half-written file - a corrupt source file is a much worse
    outcome for autonomy than a failed write;
  * newline + encoding are **preserved** unless told otherwise, so a one-line
    edit doesn't turn into a 4000-line CRLF diff;
  * reads are **bounded and paged** (offset/limit/bytes) with a `next` cursor;
  * everything mutating returns a `undo_token` when journaling is on.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import E, SkeletonKeyError
from ..core.util import clip as clip_text
from .sandbox import PathSandbox, Resolved, _dirname, _glob_to_re

DEFAULT_MAX_READ = 2_000_000


@dataclass
class ReadResult:
    path: str
    abs_path: str
    content: str
    bytes: int
    lines: int
    sha256: str
    encoding: str
    newline: str
    offset: int
    truncated: bool
    next_offset: int | None = None
    mtime: float | None = None
    size: int = 0
    is_binary: bool = False
    notes: list[str] = field(default_factory=list)
    via: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "content": self.content, "lines": self.lines,
                               "bytes": self.bytes, "sha256": self.sha256[:16], "encoding": self.encoding,
                               "newline": self.newline, "offset": self.offset}
        if self.truncated:
            out["truncated"] = True
            out["next_offset"] = self.next_offset
            out["read_hint"] = f"call again with offset={self.next_offset}" if self.next_offset is not None else ""
        if self.is_binary:
            out["is_binary"] = True
        if self.via:
            out["via"] = self.via
        if self.notes:
            out["notes"] = self.notes
        return out


@dataclass
class WriteResult:
    path: str
    created: bool
    bytes_before: int
    bytes_after: int
    sha_before: str | None
    sha_after: str
    changed: bool
    undo_token: str | None = None
    newline: str = "lf"
    encoding: str = "utf-8"
    notes: list[str] = field(default_factory=list)
    via: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], False, "")}


SNIFF_LIMIT = 8192


def _newline_of(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "crlf" if crlf > lf else ("lf" if lf else "none")


def _utf16_endianness(sample: bytes) -> str | None:
    """Detect UTF-16 with no BOM, which otherwise reads as 'binary'.

    Windows tooling writes it constantly (`Out-File` on 5.1, many editors' "Unicode"
    setting). ASCII-in-UTF-16 has NULs on every second byte, so the pattern is
    unmistakable - and mistaking it for a PNG means refusing a file we could read.
    """
    head = sample[:512]
    if len(head) < 16:
        return None
    half = len(head) // 2
    nul_odd = sum(1 for i in range(1, len(head), 2) if head[i] == 0)
    nul_even = sum(1 for i in range(0, len(head), 2) if head[i] == 0)
    printable_odd = sum(1 for i in range(0, len(head), 2) if 8 <= head[i] < 127)
    printable_even = sum(1 for i in range(1, len(head), 2) if 8 <= head[i] < 127)
    if nul_odd >= half * 0.85 and printable_odd >= half * 0.8:
        return "le"
    if nul_even >= half * 0.85 and printable_even >= half * 0.8:
        return "be"
    return None


def sniff(raw: bytes) -> tuple[str, str, bool]:
    """Return (encoding, dominant_newline, looks_binary) without extra deps."""
    if not raw:
        return "utf-8", "lf", False
    sample = raw[:SNIFF_LIMIT]
    if sample[:3] == b"\xef\xbb\xbf":
        text = sample.decode("utf-8-sig", "ignore")
        return "utf-8-sig", _newline_of(text), False
    if sample[:2] in (b"\xff\xfe", b"\xfe\xff"):
        enc = "utf-16-le" if sample[:2] == b"\xff\xfe" else "utf-16-be"
        text = sample.decode(enc, "ignore")
        return enc, _newline_of(text), False
    if b"\x00" in sample:
        enc = _utf16_endianness(sample)
        if enc:
            text = sample[2 if sample[:2] in (b"\xff\xfe", b"\xfe\xff") else 0:].decode(
                f"utf-16-{enc}", "ignore")
            return f"utf-16-{enc}", _newline_of(text), False
        # NULs that are not UTF-16 shape: treat as binary and say so upstream
        return "utf-8", _newline_of(sample.decode("latin-1")), True
    weird = sum(1 for b in sample if b < 9 or (13 < b < 32 and b != 27))
    if weird > max(4, len(sample) // 40):
        return "utf-8", "lf", True
    try:
        text = sample.decode("utf-8")
        return "utf-8", _newline_of(text), False
    except UnicodeDecodeError:
        pass
    for enc in ("cp1252", "latin-1"):
        try:
            text = sample.decode(enc)
            return enc, _newline_of(text), False
        except UnicodeDecodeError:
            continue
    return "utf-8", "lf", True


class Fs:
    def __init__(self, sandbox: PathSandbox, *, atomic: bool = True, newline_policy: str = "preserve",
                 encoding: str = "utf-8", max_read_bytes: int = DEFAULT_MAX_READ,
                 max_write_bytes: int = 20_000_000, journal: Any = None,
                 delete_mode: str = "journal") -> None:
        self.sb = sandbox
        self.atomic = atomic
        self.newline_policy = newline_policy
        self.encoding = encoding
        self.max_read_bytes = max_read_bytes
        self.max_write_bytes = max_write_bytes
        self.journal = journal
        if delete_mode not in ("journal", "os-trash", "delete"):
            raise ValueError(f"delete_mode must be journal | os-trash | delete, got {delete_mode!r}")
        self.delete_mode = delete_mode

    # --------------------------------------------------------------------- read
    def read(self, path: str, *, offset: int = 0, limit_lines: int | None = None,
             limit_bytes: int | None = None, as_: str = "text", start_line: int | None = None,
             end_line: int | None = None) -> ReadResult:
        """Read text by line offset (default) or byte range.

        `offset` is 0-based lines; `start_line`/`end_line` are 1-based inclusive
        (matches what agents see in editor diffs and in `rg -n` output).
        """
        res = self.sb.resolve(path, intent="read")
        if res.is_dir:
            raise SkeletonKeyError(E.BAD_ARGS, f"{res.display} is a directory",
                                   details={"path": res.display, "next": "use fs.list"},
                                   next_actions=[{"tool": "fs.list", "args": {"path": res.display}}])
        try:
            size = os.path.getsize(res.real)
        except OSError as exc:
            raise SkeletonKeyError(E.ENOENT, f"cannot stat {res.display}: {exc}",
                                   details={"path": res.display, "suggested": self._suggest(res)}) from exc

        cap = min(self.max_read_bytes, limit_bytes or self.max_read_bytes)
        # Simplicity beats cleverness here: read the whole file when it fits the
        # cap (the overwhelmingly common case for source files) and slice in
        # memory. Only oversized files take the streaming path.
        whole = size <= cap
        raw, reached_end = (self._read_all(res.real, cap=cap), True) if whole else self._read_window(
            res.real, cap=cap, offset=offset, limit_lines=limit_lines, start_line=start_line, end_line=end_line)
        enc, nl, binary = sniff(raw)

        if binary and as_ != "text-forced":
            return ReadResult(path=res.display, abs_path=res.abs,
                              content=clip_text(raw.decode(enc, "replace"), cap), bytes=len(raw),
                              lines=raw.count(b"\n"), sha256=self.checksum(res.display), encoding=enc,
                              via=res.via(),
                              newline=nl, offset=offset, truncated=size > len(raw), is_binary=True,
                              size=size, notes=["binary content; decoded with replacement chars for display"])

        text = raw.decode(enc, "replace").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if text.endswith("\n"):
            lines = lines[:-1]

        if whole:
            if start_line is not None or end_line is not None:
                a = max(1, start_line or 1)
                b = min(len(lines), end_line or len(lines)) or len(lines)
                shown, next_off = lines[a - 1:b], None
            else:
                take = limit_lines if limit_lines and limit_lines > 0 else len(lines)
                shown = lines[offset:offset + take]
                next_off = offset + take if offset + take < len(lines) else None
        else:
            # streaming path already returned exactly the requested window; do not
            # slice again or we double-apply the offset.
            shown = lines
            take = limit_lines or (1 + (end_line or 0) - (start_line or 1)) or len(shown)
            next_off = offset + take if not reached_end else None

        body = "\n".join(shown)
        if body and nl == "crlf":
            body = body.replace("\n", "\r\n")
        # Full-file digest only when we actually saw the whole file; otherwise the
        # agent must not mistake a window hash for a content hash.
        if whole:
            sha, sha_scope = hashlib.sha256(raw).hexdigest(), "file"
        else:
            sha, sha_scope = hashlib.sha256(raw).hexdigest(), "window"
        notes: list[str] = []
        if sha_scope == "window":
            notes.append("sha256 covers the returned window only (file larger than the read cap)")
        total_lines = len(lines)
        if whole:
            notes.append(f"total_lines={total_lines}") if total_lines != len(shown) else None
        else:
            notes.append("total_lines=unknown (file exceeds read cap)")
        return ReadResult(
            path=res.display, abs_path=res.abs, content=(body + "\n") if body else "",
            bytes=len(raw), lines=len(shown), sha256=sha, encoding=enc,
            newline="lf" if nl == "none" else nl, offset=offset,
            truncated=next_off is not None, next_offset=next_off, mtime=res.mtime, size=size,
            notes=[n for n in notes if n], via=res.via(),
        )

    @staticmethod
    def _read_all(real: str, *, cap: int) -> bytes:
        with open(real, "rb") as fh:
            return fh.read(cap)

    def _read_window(self, real: str, *, cap: int, offset: int, limit_lines: int | None,
                     start_line: int | None, end_line: int | None) -> tuple[bytes, bool]:
        """O(cap) line window for files too big to read whole. (bytes, reached_eof)."""
        first = max(0, (start_line - 1) if start_line is not None else offset)
        count = None
        if start_line is not None or end_line is not None:
            count = ((end_line or 1 << 30) - (start_line or 1) + 1)
        elif limit_lines:
            count = limit_lines
        out: list[bytes] = []
        total = 0
        seen = 0
        reached = True
        with open(real, "rb") as fh:
            for line in fh:
                if first <= seen and (count is None or seen < first + count):
                    out.append(line)
                    total += len(line)
                    if total >= cap:
                        reached = False
                        break
                seen += 1
                if count is not None and seen >= first + count:
                    # peek: "is there more?" is what decides `truncated` for the caller
                    reached = not fh.readline()
                    break
                if seen > 500_000 and not out:
                    # window sits past a pathological prefix: stop, don't hang
                    reached = False
                    break
        return b"".join(out), reached

    def read_bytes(self, path: str, *, limit: int | None = None, offset: int = 0) -> tuple[bytes, int, bool]:
        res = self.sb.resolve(path, intent="read")
        cap = min(self.max_read_bytes, limit or self.max_read_bytes)
        with open(res.real, "rb") as fh:
            if offset:
                fh.seek(offset)
            data = fh.read(cap)
        total = os.path.getsize(res.real)
        return data, offset + len(data), offset + len(data) < total

    def sniff(self, path: str, *, sample_bytes: int = SNIFF_LIMIT) -> dict[str, Any]:
        """Report what a file *is* before anyone decides how to read it.

        Cheap insurance against the two expensive mistakes: pulling a 40 MB UTF-16
        log or a PNG into model context. Read-only, one small sample, no guesses -
        `confidence` tells the caller when to look again rather than trust us.
        """
        res = self.sb.resolve(path, intent="read")
        size = os.path.getsize(res.real)
        raw, consumed, more = self.read_bytes(path, limit=max(256, int(sample_bytes)))
        encoding, newline, binary = sniff(raw)
        st = os.stat(res.real)
        first = raw.split(b"\n", 1)[0][:200].decode(encoding if not binary else "utf-8", "replace")
        out: dict[str, Any] = {
            "path": path, "bytes": size, "via": res.via(), "encoding": encoding, "newline": newline,
            "binary": binary, "has_bom": raw[:3] == b"\xef\xbb\xbf" or raw[:2] in (b"\xff\xfe", b"\xfe\xff"),
            "sampled_bytes": consumed, "sample_truncated": more,
            "lines_estimate": (raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0))
                              + (1 if more else 0),
            "first_line": first.rstrip("\r"),
            "readable_as_text": not binary,
            "mode": oct(st.st_mode & 0o777), "mtime": int(st.st_mtime),
        }
        if binary:
            out["advice"] = ("binary content: do not fs.read this. Use fs.search's file list, "
                             "a format-specific extractor, or shell.run on the path")
        elif encoding in ("utf-16-le", "utf-16-be"):
            out["advice"] = ("UTF-16: fs.read decodes it, but any shell redirect you write back "
                             "will not be byte-faithful. Round-trip through fs.write with encoding set.")
        elif newline == "crlf":
            out["advice"] = ("CRLF file: keep it that way (fs.write preserves it). A patch whose "
                             "old_text was written with LF still matches, but new_text gains LF endings.")
        if more and size > 1_000_000:
            out["advice"] = (out.get("advice", "") +
                             f" Large file ({size} bytes): use fs.read with offset/limit_lines.").strip()
        return out

    def checksum(self, path: str, *, algo: str = "sha256") -> str:
        res = self.sb.resolve(path, intent="read")
        h = hashlib.new(algo)
        with open(res.real, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # -------------------------------------------------------------------- write
    def write(self, path: str, content: str | bytes, *, overwrite: bool = True,
              create_dirs: bool = True, newline: str | None = None, encoding: str | None = None,
              expect_sha: str | None = None, dry_run: bool = False, task_id: str = "") -> WriteResult:
        res = self.sb.resolve(path, intent="create" if not overwrite else "write")
        exists = res.exists
        if exists and not overwrite:
            raise SkeletonKeyError(E.EEXIST, f"{res.display} already exists",
                                   details={"path": res.display, "size": res.size,
                                            "advice": "pass overwrite=true (or use fs.patch for edits)"},
                                   next_actions=[{"tool": "fs.patch", "args": {"path": res.display}}])
        if not exists and not create_dirs:
            parent = _dirname(res.real)
            if not os.path.isdir(parent):
                raise SkeletonKeyError(E.ENOENT, f"parent directory missing: {_display(parent, self.sb)}",
                                       details={"path": res.display, "advice": "create_dirs=true to make it"})
        raw = content.encode(encoding or self.encoding, "surrogateescape") if isinstance(content, str) else bytes(content)
        if len(raw) > self.max_write_bytes:
            raise SkeletonKeyError(E.TOO_LARGE, f"payload {len(raw)}B exceeds max_write_bytes",
                                   details={"bytes": len(raw), "limit": self.max_write_bytes})

        target_enc, detected_nl, binary = (encoding or self.encoding), "lf", isinstance(content, bytes)
        if not binary:
            try:
                with open(res.real, "rb") as fh:
                    head = fh.read(SNIFF_LIMIT)
                target_enc, detected_nl, _ = sniff(head)
            except OSError:
                target_enc, detected_nl = encoding or self.encoding, "lf"
        policy_nl = newline or self.newline_policy
        effective_nl = detected_nl if policy_nl == "preserve" else policy_nl
        if effective_nl == "crlf" and not binary:
            raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        elif effective_nl == "lf" and not binary:
            raw = raw.replace(b"\r\n", b"\n")

        sha_before: str | None = None
        if exists:
            sha_before = self.checksum(path)
            if expect_sha and not sha_before.startswith(expect_sha[:16]) and sha_before != expect_sha:
                raise SkeletonKeyError(
                    E.CONFLICT, f"{res.display} changed since you read it",
                    details={"path": res.display, "expected_sha": expect_sha, "actual_sha": sha_before,
                             "advice": "re-read the file and re-derive the edit"},
                    next_actions=[{"tool": "fs.read", "args": {"path": res.display}}],
                )
        result = WriteResult(path=res.display, created=not exists, bytes_before=res.size or 0,
                             bytes_after=len(raw), sha_before=sha_before,
                             sha_after=hashlib.sha256(raw).hexdigest(),
                             changed=(sha_before != hashlib.sha256(raw).hexdigest()),
                             newline=effective_nl, encoding=target_enc, via=res.via())
        if dry_run:
            result.notes.append("dry_run: nothing written")
            return result
        if exists and self.journal is not None:
            result.undo_token = self.journal.record_before(res, raw, action="write", task_id=task_id)
        elif self.journal is not None:
            # the after-image is the file's new content itself - that is what a redo recreates
            result.undo_token = self.journal.record_new(res, action="create", task_id=task_id,
                                                        upcoming_bytes=raw)
        self._atomic_write(res.real, raw, create_dirs=create_dirs, encoding=target_enc if not binary else None)
        return result

    def _atomic_write(self, target: str, raw: bytes, *, create_dirs: bool, encoding: str | None) -> None:
        parent = _dirname(target) or "."
        if create_dirs:
            os.makedirs(parent, exist_ok=True)
        if not self.atomic:
            with open(target, "wb") as fh:
                fh.write(raw)
            return
        tmp = f"{target}.sk-tmp-{os.getpid()}-{int(time.time() * 1000) % 100000}"
        try:
            with open(tmp, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            _copy_mode(target, tmp)
            if os.name == "nt":
                # Windows os.replace fails if the target is open elsewhere; surface
                # that as a retryable lock error instead of a generic IO error.
                try:
                    os.replace(tmp, target)
                except PermissionError as exc:
                    with_suppress(tmp)
                    raise SkeletonKeyError(
                        E.PATH_UNREADABLE, f"target is locked by another process: {_display(target, self.sb)}",
                        details={"path": _display(target, self.sb), "holder_hint": "close the editor/handle or retry",
                                 "retryable": True},
                    ) from exc
            else:
                os.replace(tmp, target)
            _fsync_dir(parent)
        except OSError as exc:
            with_suppress(tmp)
            raise SkeletonKeyError(E.IO, f"atomic write failed: {exc}",
                                   details={"path": _display(target, self.sb), "errno": exc.errno}) from exc

    # -------------------------------------------------------------------- patch
    def patch(self, path: str, edits: list[dict[str, Any]], *, dry_run: bool = False,
              strategy: str = "exact-then-fuzzy", task_id: str = "", expect_sha: str | None = None) -> dict[str, Any]:
        """Apply ordered find/replace edits with preconditions.

        edit = {old_text, new_text, replace_all?, occurrence?, line_hint?}
        Fuzzy fallback tolerates whitespace-only differences so an agent that
        reflowed its snippet still lands, but never across a real mismatch.
        """
        if not edits:
            raise SkeletonKeyError(E.BAD_ARGS, "patch requires at least one edit",
                                   details={"schema_hint": {"old_text": "str", "new_text": "str"}})
        full = self.read(path, as_="text")
        original = full.content
        text = original
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for i, edit in enumerate(edits):
            prev_text = text          # line numbers are relative to what this edit saw
            old = edit.get("old_text")
            new = edit.get("new_text")
            if not isinstance(old, str) or not isinstance(new, str):
                failed.append({"index": i, "error": "old_text and new_text must both be strings",
                               "received": [type(old).__name__, type(new).__name__]})
                continue
            if old == new:
                failed.append({"index": i, "error": "old_text equals new_text (no-op edit)"})
                continue
            all_flag = bool(edit.get("replace_all"))
            count = text.count(old)
            how = "exact"
            if count == 0:
                text2, count2 = _fuzzy_replace(text, old, new, all_=all_flag)
                if count2:
                    text, count, how = text2, count2, "fuzzy-whitespace"
            if count == 0:
                failed.append({"index": i, "error": "old_text not found",
                               "nearest": _nearest_lines(original, old),
                               "hint": "re-read the file; do not guess indentation"})
                continue
            if count > 1 and not all_flag:
                occ = edit.get("occurrence")
                if isinstance(occ, int) and 1 <= occ <= count:
                    text = _replace_nth(text, old, new, occ)
                    how = f"exact@{occ}"
                else:
                    failed.append({"index": i, "error": E.AMBIGUOUS_MATCH.code,
                                   "matches": count, "at_lines": _occurrence_lines(prev_text, old),
                                   "how": "add more surrounding context or set replace_all=true"})
                    continue
            else:
                # `replace_all` and a unique anchor land on the same call: with
                # count > 1 handled above, there is nothing left to distinguish.
                text = text.replace(old, new)
            applied.append({"index": i, "matched": count, "strategy": how,
                            "at_line": (_occurrence_lines(prev_text, old, 1) or [None])[0],
                            "lines_delta": text.count("\n") - original.count("\n")})
        if not applied:
            first = (failed or [{}])[0]
            reason = str(first.get("error", ""))
            if reason == "old_text not found":
                code = E.PATCH_CONFLICT
            elif reason == E.AMBIGUOUS_MATCH.code:
                code = E.AMBIGUOUS_MATCH
            else:
                code = E.BAD_ARGS
            raise SkeletonKeyError(
                code, f"no edits applied to {full.path}: {reason or 'unknown'} ({len(failed)} failed)",
                details={"path": full.path, "failures": failed, "sha": full.sha256},
                next_actions=[{"tool": "fs.read", "args": {"path": full.path, "limit_lines": 200}}],
            )
        if full.newline == "crlf":
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        wr = self.write(path, text.replace("\r\n", "\n") if full.newline != "crlf" else text,
                        overwrite=True, dry_run=dry_run, expect_sha=expect_sha or full.sha256,
                        newline=full.newline, task_id=task_id)
        return {"path": full.path, "applied": len(applied), "failed": failed, "edits": applied,
                "write": wr.to_dict(), "dry_run": dry_run, "via": full.via,
                "unified_diff": unified_diff(original, text, full.path, max_lines=240)}

    # -------------------------------------------------------------- list/glob
    def list(self, path: str = ".", *, depth: int = 1, sort: str = "name",
             include_hidden: bool | None = None, limit: int = 400,
             types: list[str] | None = None) -> dict[str, Any]:
        res = self.sb.resolve(path, intent="list")
        if res.is_file:
            return {"path": res.display, "entries": [entry_info(res, self.sb)], "truncated": False,
                    "via": res.via()}
        out: list[dict[str, Any]] = []
        truncated = False
        hidden = include_hidden
        start_depth = depth

        def walk(dir_real: str, dir_rel: str, level: int) -> None:
            nonlocal truncated
            if truncated or level > start_depth:
                return
            try:
                names = sorted(os.listdir(dir_real), key=str.lower)
            except OSError:
                return
            for name in names:
                if len(out) >= limit:
                    truncated = True
                    return
                if hidden is None:
                    if name.startswith(".") and name not in (".", ".."):
                        continue
                elif not hidden and name.startswith("."):
                    continue
                child_rel = os.path.join(dir_rel, name) if dir_rel else name
                if self.sb.should_ignore(child_rel):
                    continue
                child_abs = os.path.join(dir_real, name)
                try:
                    st = os.lstat(child_abs)
                except OSError:
                    continue
                kind = "dir" if stat.S_ISDIR(st.st_mode) else ("link" if stat.S_ISLNK(st.st_mode) else "file")
                if types and kind not in types:
                    pass
                else:
                    out.append({"name": child_rel.replace("\\", "/"), "kind": kind,
                                "bytes": st.st_size if kind == "file" else None, "mtime": st.st_mtime})
                if kind == "dir" and level < start_depth:
                    walk(child_abs, child_rel, level + 1)

        walk(res.real, "" if res.rel in (".", "") else res.rel, 1)
        if sort == "size":
            out.sort(key=lambda e: -(e.get("bytes") or 0))
        elif sort == "mtime":
            out.sort(key=lambda e: -e["mtime"])
        return {"path": res.display, "entries": out, "count": len(out), "truncated": truncated,
                "via": res.via(),
                **({"hint": "limit hit; raise limit or narrow path"} if truncated else {})}

    def glob(self, pattern: str, *, root: str = ".", limit: int = 500,
             sort: str = "mtime") -> dict[str, Any]:
        rx = _glob_to_re(pattern)
        res = self.sb.resolve(root, intent="list")
        base = res.real
        matches: list[dict[str, Any]] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and not self.sb.should_ignore(
                os.path.relpath(os.path.join(dirpath, d), base))]
            for fn in filenames:
                scanned += 1
                if scanned > 200_000:
                    break
                abs_child = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_child, base).replace(os.sep, "/")
                if self.sb.should_ignore(rel):
                    continue
                if rx.search(rel.lower()):
                    try:
                        st = os.stat(abs_child)
                    except OSError:
                        continue
                    matches.append({"path": rel, "bytes": st.st_size, "mtime": st.st_mtime})
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit or scanned > 200_000:
                break
        # tie-break on path: files created in the same instant share an mtime,
        # and a stable answer is the only one a replay can reproduce
        matches.sort(key=lambda m: (-m["mtime"], m["path"]) if sort == "mtime" else m["path"])
        return {"pattern": pattern, "root": res.display, "matches": matches, "count": len(matches),
                "truncated": len(matches) >= limit, "scanned": scanned, "via": res.via()}

    # ------------------------------------------------------------------ mutate
    def move(self, src: str, dst: str, *, overwrite: bool = False, dry_run: bool = False,
             task_id: str = "") -> dict[str, Any]:
        a = self.sb.resolve(src, intent="delete")
        b = self.sb.resolve(dst, intent="write")
        if not a.exists:
            raise SkeletonKeyError(E.ENOENT, f"{a.display} does not exist", details={"path": a.display})
        if b.exists and not overwrite:
            raise SkeletonKeyError(E.EEXIST, f"{b.display} exists", details={"src": a.display, "dst": b.display})
        undo = self.journal.record_move(a, b, task_id=task_id) if self.journal else None
        via = {"src": a.via(), "dst": b.via()}
        if dry_run:
            return {"src": a.display, "dst": b.display, "dry_run": True, "undo_token": undo, "via": via}
        os.makedirs(_dirname(b.real) or ".", exist_ok=True)
        try:
            shutil.move(a.real, b.real)
        except OSError as exc:
            raise SkeletonKeyError(E.IO, f"move failed: {exc}", details={"src": a.display, "dst": b.display}) from exc
        return {"src": a.display, "dst": b.display, "undo_token": undo, "moved": True, "via": via}

    def delete(self, path: str, *, recursive: bool = False, dry_run: bool = False,
               task_id: str = "") -> dict[str, Any]:
        res = self.sb.resolve(path, intent="delete")
        if not res.exists:
            raise SkeletonKeyError(E.ENOENT, f"{res.display} does not exist",
                                   details={"path": res.display, "suggested": self._suggest(res)})
        if res.is_dir and not recursive:
            try:
                non_empty = bool(os.listdir(res.real))
            except OSError:
                non_empty = True
            if non_empty:
                raise SkeletonKeyError(
                    E.BAD_ARGS, f"{res.display} is a non-empty directory - pass recursive=true to delete it "
                               f"(the journal still makes it undoable)",
                    details={"path": res.display, "children": len(os.listdir(res.real)),
                             "advice": "recursive=true; undo is available via the returned undo_token"},
                )
        mode = self.delete_mode
        if mode == "os-trash" and not self._trash_available():
            # Probe *before* anything is recorded: a host without a trash API must
            # report it and delete nothing - not leave a journal entry for a
            # deletion that never happened.
            raise SkeletonKeyError(
                E.UNSUPPORTED_PLATFORM,
                "fs.trash = \"os-trash\" needs the platform recycle bin, and this host has no trash API on PATH",
                details={"path": res.display, "fs_trash": mode,
                         "advice": 'set fs.trash = "journal" for an undoable hard delete, '
                                   "or install the platform trash API (glib provides `gio`)"},
            )
        snapshot = None
        if self.journal is not None and mode != "delete":
            # "delete" tier is the hard, unjournaled delete; the other tiers keep the
            # journal - for os-trash it is the *second* copy, so the OS bin can be
            # emptied without the change becoming irreversible.
            snapshot = self.journal.record_delete(res, recursive=recursive, task_id=task_id)
        if dry_run:
            return {"path": res.display, "dry_run": True, "would_delete": True, "undo_token": snapshot,
                    "mode": mode, "via": res.via()}
        try:
            if mode == "os-trash":
                self._os_trash(res)
            elif res.is_dir:
                shutil.rmtree(res.real)
            else:
                os.unlink(res.real)
        except OSError as exc:
            if self.journal is not None:
                self.journal.discard(snapshot)
            raise SkeletonKeyError(E.IO, f"delete failed: {exc}", details={"path": res.display}) from exc
        return {"path": res.display, "deleted": True, "kind": "dir" if res.is_dir else "file",
                "undo_token": snapshot, "recoverable": bool(snapshot), "mode": mode,
                "via": res.via(),
                **({"trash": "recycle bin"} if mode == "os-trash" else {})}

    # ------------------------------------------------------------- deletion tiers
    @staticmethod
    def os_trash_command(abs_path: str, *, win: bool | None = None) -> list[str]:
        """The argv that moves `abs_path` to the platform recycle bin.

        Windows: PowerShell's Shell.Application (the recycle bin is namespace 10).
        Everywhere else: `gio trash`. A pure function of the path so the rendered
        payload is testable on any host; `win` overrides the platform for tests.
        """
        is_win = os.name == "nt" if win is None else win
        if is_win:
            pwsh = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
            script = (
                "$ErrorActionPreference = 'Stop';"
                "$sh = New-Object -ComObject Shell.Application;"
                f"$parent = Split-Path -LiteralPath '{abs_path}';"
                f"$leaf = Split-Path -LiteralPath '{abs_path}' -Leaf;"
                "$item = $sh.Namespace($parent).ParseName($leaf);"
                f"if ($null -eq $item) {{ throw 'path not found: {abs_path}' }};"
                "$sh.Namespace(10).MoveHere($item.Path) | Out-Null;"
                f"if (Test-Path -LiteralPath '{abs_path}') {{ throw 'still present after MoveHere: {abs_path}' }};"
                "Write-Output 'trashed'"
            )
            return [pwsh, "-NoProfile", "-NonInteractive", "-Command", script]
        return ["gio", "trash", abs_path]

    def _trash_available(self) -> bool:
        if os.name == "nt":
            return bool(shutil.which("pwsh") or shutil.which("powershell"))
        return bool(shutil.which("gio"))

    def _os_trash(self, res: Resolved) -> None:
        """Move the resolved path into the recycle bin (the file is already journaled)."""
        argv = self.os_trash_command(res.real)
        exe = os.path.basename(argv[0])
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise SkeletonKeyError(
                E.IO, f"the trash API timed out moving {res.display}",
                details={"path": res.display,
                         "note": "the file may still be in place - check it before retrying"},
            ) from exc
        except OSError as exc:
            raise SkeletonKeyError(
                E.UNSUPPORTED_PLATFORM, f"the trash API ({exe}) could not start: {exc}",
                details={"path": res.display},
            ) from exc
        if proc.returncode != 0 or os.path.exists(res.real):
            tail = (proc.stderr or proc.stdout or "").strip()[:200]
            raise SkeletonKeyError(
                E.IO, f"could not move {res.display} to the recycle bin: {tail or proc.returncode}",
                details={"path": res.display, "exit_code": proc.returncode, "command": exe},
            )

    def mkdir(self, path: str, *, parents: bool = True, dry_run: bool = False,
              task_id: str = "") -> dict[str, Any]:
        res = self.sb.resolve(path, intent="write")
        if res.exists:
            return {"path": res.display, "created": False, "already": True, "via": res.via()}
        if dry_run:
            return {"path": res.display, "created": False, "dry_run": True,
                    "would_create": True, "via": res.via()}
        if not parents and os.path.dirname(res.real) and not os.path.isdir(os.path.dirname(res.real)):
            raise SkeletonKeyError(
                E.BAD_ARGS, f"parent of {res.display} does not exist and parents=false",
                details={"path": res.display, "parent": os.path.dirname(res.display)},
            )
        # Remember which levels we created: `parents` can build a chain, and undo
        # must be able to say "I only made the leaf" rather than rmtree a tree.
        missing = []
        probe = res.real
        while probe and not os.path.isdir(probe):
            missing.append(probe)
            nxt = os.path.dirname(probe)
            if nxt == probe:
                break
            probe = nxt
        os.makedirs(res.real, exist_ok=True)
        out: dict[str, Any] = {"path": res.display, "created": True, "via": res.via(),
                               "created_dirs": [self.sb.display(p) for p in reversed(missing)]
                               if hasattr(self.sb, "display") else list(reversed(missing))}
        if self.journal is not None:
            token = self.journal.record_new(res, action="create", task_id=task_id)
            if token:
                out["undo_token"] = token
                out["undo"] = {"tool": "fs.undo", "args": {"token": token}}
        return out

    def chmod(self, path: str, mode: str | int, *, recursive: bool = False,
              dry_run: bool = False, task_id: str = "") -> dict[str, Any]:
        """Set mode bits, journalled so `fs.undo` puts the old mode back.

        Every target is resolved through the sandbox *before* anything is written, so a
        denied path inside a recursive walk refuses the whole call rather than half-applying.
        A mode that already matches records nothing: idempotency should be visible in the
        journal, not disguised as a restore point.
        """
        res = self.sb.resolve(path, intent="write")
        targets, truncated = self._chmod_targets(res, recursive=recursive)
        plans: list[tuple[Resolved, int, int]] = []
        for t in targets:
            if not t.exists:
                raise SkeletonKeyError(
                    E.ENOENT, f"{t.display} does not exist",
                    details={"path": t.display, "suggested": self._suggest(t),
                             "advice": "chmod needs a real inode; a missing path is not the same "
                                       "as an empty one"},
                )
            try:
                current = stat.S_IMODE(os.stat(t.real).st_mode)
            except OSError as exc:
                raise SkeletonKeyError(E.IO, f"could not read the mode of {t.display}: {exc}",
                                       details={"path": t.display}) from exc
            plans.append((t, current, _parse_mode(mode, current)))
        if dry_run:
            would = [{"path": t.display, "from": oct(cur), "to": oct(want),
                      "changed": cur != want} for t, cur, want in plans]
            return {"path": res.display, "dry_run": True, "targets": would, "via": res.via(),
                    "would_chmod": oct(plans[0][2]), "changed_count": sum(1 for p in would if p["changed"])}
        applied, skipped, failures = [], [], []
        for t, current, want in plans:
            if current == want:
                skipped.append(t.display)
                continue
            token = (self.journal.record_meta(t, task_id=task_id, mode_after=want)
                     if self.journal else "")
            try:
                os.chmod(t.real, want)
            except (OSError, NotImplementedError) as exc:
                if self.journal and token:
                    self.journal.discard(token)
                raise SkeletonKeyError(
                    E.UNSUPPORTED_PLATFORM, f"chmod failed on {t.display}: {exc}",
                    details={"path": t.display, "os": os.name, "requested": oct(want),
                             "advice": "on Windows, mode bits collapse to the read-only "
                                       "attribute; use ACLs (icacls) for real permissions"},
                ) from exc
            try:
                actual = stat.S_IMODE(os.stat(t.real).st_mode)
            except OSError:
                actual = want
            row = {"path": t.display, "from": oct(current), "mode": oct(want)}
            if actual != want:
                # Windows reports success for bits it does not store. Say so instead of
                # letting the agent believe 0o600 means "nobody else can read this".
                row["effective"] = oct(actual)
                row["note"] = ("the filesystem did not keep every bit; this is expected on "
                               "Windows, where chmod sets the read-only attribute and ACLs "
                               "do the real work")
                failures.append(row)
            if token:
                row["undo_token"] = token
            applied.append(row)
        out: dict[str, Any] = {
            "path": res.display, "mode": oct(plans[0][2]), "via": res.via(),
            "mode_before": oct(plans[0][1]), "changed": bool(applied),
            "targets": applied, "count": len(applied), "unchanged": len(skipped),
        }
        if truncated:
            out["truncated"] = True
            out["hint"] = f"recursive chmod stopped at {MAX_CHMOD_TARGETS} paths"
        if failures:
            out["partial_apply"] = failures
            out["next_actions"] = [{
                "tool": "shell.run",
                "note": "template, not a verified recipe - confirm the principal and rights "
                        "before running it on a machine you care about",
                "args": {"dialect": "pwsh",
                         "script": "icacls $args[0] /inheritance:r /grant:r $args[1]",
                         "argv": [res.real, 'BUILTIN\\Users:(OI)(CI)R']}}]
        # `undo_token` at the top level is the convention every other mutating fs tool
        # follows, so an agent that only reads one key still gets a way back. A recursive
        # chmod has one token per changed path, and `fs.undo_task` is the honest way to
        # reverse all of them.
        tokens = [row["undo_token"] for row in applied if row.get("undo_token")]
        if tokens:
            out["undo_token"] = tokens[0]
            out["undo_tokens"] = tokens
            out["undo"] = {"tool": "fs.undo", "args": {"token": tokens[0]}}
            if len(tokens) > 1:
                out["undo"]["note"] = (f"this reverts only {out['targets'][0]['path']}; the other "
                                       f"{len(tokens) - 1} changed paths are in undo_tokens, or "
                                       f"undo them together with fs.undo_task {out.get('task_id')!r}")
        return out

    def _chmod_targets(self, res: Resolved, *, recursive: bool) -> tuple[list[Resolved], bool]:
        if not recursive:
            return [res], False
        if not res.is_dir:
            raise SkeletonKeyError(
                E.BAD_ARGS, f"{res.display} is not a directory, so recursive=true has nothing to walk",
                details={"path": res.display, "advice": "drop recursive=true for a single file"},
            )
        out = [res]
        truncated = False
        for root, dirs, files in os.walk(res.real):
            # Symlinks are not descended into: `chmod -R` through a link is how a
            # workspace-internal directory ends up changing /etc.
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
            for name in sorted(dirs) + sorted(files):
                full = os.path.join(root, name)
                if os.path.islink(full):
                    continue
                out.append(self.sb.resolve(full, intent="write"))
                if len(out) >= MAX_CHMOD_TARGETS:
                    return out, True
        return out, truncated


    def stat(self, path: str) -> dict[str, Any]:
        return self.sb.resolve(path, intent="read").to_dict()

    def _suggest(self, res: Resolved, *, limit: int = 5) -> list[str]:
        """Nearby names for a typo - cheap, and it stops retry loops burning turns."""
        parent = os.path.dirname(res.real)
        want = os.path.basename(res.real).lower()
        try:
            names = os.listdir(parent)
        except OSError:
            return []
        scored = []
        for n in names:
            ln = n.lower()
            s = 0.0
            if ln == want:
                s = 1.0
            elif want and (want in ln or ln in want):
                s = 0.7
            else:
                common = len({*want} & {*ln}) / max(1, len(set(want) | set(ln)))
                s = common * 0.6
            if s > 0.3:
                scored.append((s, n))
        scored.sort(key=lambda x: -x[0])
        return [os.path.join(res.display.rsplit("/", 1)[0] if "/" in res.display else "", n) or n
                for _s, n in scored[:limit]]


# --------------------------------------------------------------------- helpers


def entry_info(res: Resolved, sb: PathSandbox) -> dict[str, Any]:
    return {"name": res.display, "kind": "dir" if res.is_dir else "file", "bytes": res.size,
            "mtime": res.mtime, "mode": stat.filemode(res.mode)[1:] if res.mode else None}


def _display(path: str, sb: PathSandbox) -> str:
    try:
        return sb.resolve(path, intent="read").display
    except SkeletonKeyError:
        return path


_OCTAL_MODE = re.compile(r"0?[oO]?([0-7]{3,4})")
_CLAUSE = re.compile(r"([ugoa]*)((?:[+\-=][rwxst]*)+)")
_PAIR = re.compile(r"([+\-=])([rwxst]*)")
_BITS = {"r": 4, "w": 2, "x": 1}
_WHO_SHIFT = {"u": 6, "g": 3, "o": 0}
# Which bit each class owns. `s` means setuid for u and setgid for g; the sticky bit belongs
# to `o` and is spelled `t`. GNU chmod accepts sloppier mixtures by ignoring them, which is
# the opposite of what an agent needs - here they are refused (see _SPECIAL_BIT).
_CLASS_SPECIAL = {"u": 0o4000, "g": 0o2000, "o": 0o1000}
_SPECIAL_BIT = {("u", "s"): 0o4000, ("g", "s"): 0o2000, ("o", "t"): 0o1000}
MAX_CHMOD_TARGETS = 512


def _special_or_plain(who: str, char: str, *, lenient: bool = False) -> int | None:
    """The bit a `(who, char)` pair means, or `None` when `a` pulled in a class that lacks it."""
    if char in "st":
        bit = _SPECIAL_BIT.get((who, char))
        if bit is None:
            if lenient:
                return None          # `a-s` means "every class that has an s bit"
            raise _mode_error(
                char, who=who, char=char,
                why=f"`{who}{char}` is not a bit this toolkit models - the special bits are "
                    f"`u+s` (setuid), `g+s` (setgid) and `o+t` (sticky); `a+s`/`a-t` are accepted "
                    f"as shorthand, and octal covers anything else")
        return bit
    return _BITS[char] << _WHO_SHIFT[who]


def _apply_pair(out: int, who: str, op: str, perms: str, spec: Any, clause: str,
                *, lenient: bool = False) -> int:
    """One `who op perms` element, applied to the running mode."""
    if op == "+" and not perms:
        raise _mode_error(spec, clause=clause,
                          why="`+` needs at least one bit - use `-` or `=` to clear")
    mask = bits = 0
    for w in dict.fromkeys(who):
        shift = _WHO_SHIFT[w]
        # An empty rhs means "every rwx bit of that class" (`o-`, `go=`); a special bit is
        # only named explicitly in the perms, so `a-` cannot invent a sticky bit - while `=`
        # takes the class's own special with it, below.

        for c in (perms or "rwx"):
            bit = _special_or_plain(w, c, lenient=lenient)
            if bit is not None:
                mask |= bit
        if op == "=":
            # `=` replaces the whole triple, and the triple includes that class's special
            # bit: `a=r` is 0o444, and `o=` on a sticky dir is 0o770 with the sticky gone.
            # Cross-checked against /bin/chmod rather than against this comment.
            mask |= (0o7 << shift) | _CLASS_SPECIAL[w]
        if op != "-":
            for c in perms:
                bit = _special_or_plain(w, c, lenient=lenient)
                if bit is not None:
                    bits |= bit
    if op == "+":
        return (out | bits) & 0o7777
    if op == "-":
        return out & ~mask & 0o7777
    return (out & ~mask | bits) & 0o7777


def _parse_mode(spec: str | int, current: int = 0) -> int:
    """Turn an octal or symbolic mode into bits, applied to `current`.

    Accepted: `644`, `0644`, `0o755`, plain ints, and comma-separated symbolic clauses -
    `u+x`, `go-w`, `a=r`, `u=rw,go=r`, `a=rwx,o+t`, `u=rws`. An empty who means all three,
    per POSIX.

    Anything else raises rather than guessing. Two things this deliberately does *not* do:
    fall back to `0o644` for a spec it cannot read (the parser it replaced did, so one typo
    stripped a script's execute bit silently), and accept GNU's silently-ignored mixtures
    such as `u+t` - see `_SPECIAL_BIT`.
    """
    if isinstance(spec, bool):                       # bool is an int subclass; True is not a mode
        raise _mode_error(spec)
    if isinstance(spec, int):
        if not 0 <= spec <= 0o7777:
            raise _mode_error(spec)
        return spec
    if not isinstance(spec, str):
        raise _mode_error(spec)
    text = spec.strip()
    if not text:
        raise _mode_error(spec)
    octal = _OCTAL_MODE.fullmatch(text)
    if octal:
        return int(octal.group(1), 8) & 0o7777
    out = current & 0o7777
    who = "ugo"
    for clause in text.split(","):
        piece = clause.strip()
        cm = _CLAUSE.fullmatch(piece)
        if not cm:
            raise _mode_error(spec, clause=piece)
        who_raw, body = cm.groups()
        if who_raw:
            who = "ugo" if "a" in who_raw else who_raw
        # `a` is the one place a class may legitimately have no such bit: `a-s` clears
        # setuid and setgid and says nothing about sticky, which is what GNU does too.
        lenient = "a" in who_raw
        for op, perms in _PAIR.findall(body):
            out = _apply_pair(out, who, op, perms, spec, piece, lenient=lenient)
    return out


def _mode_error(spec: Any, **ctx: Any) -> SkeletonKeyError:
    details = {"mode": spec if isinstance(spec, (int, str)) else repr(spec),
               "accepted": ["644", "0o755", "u+x", "go-w", "a=r", "u=rw,go=r"],
               "advice": "an unparseable mode is refused, never guessed - a silent 0o644 "
                         "fallback would strip the execute bit off whatever you named"}
    details.update(ctx)
    return SkeletonKeyError(E.BAD_ARGS, f"unrecognised mode {spec!r}", details=details)


def _copy_mode(src: str, dst: str) -> None:
    if os.name == "nt":
        return
    try:
        st = os.stat(src)
        os.chmod(dst, stat.S_IMODE(st.st_mode))
    except OSError:
        try:
            os.chmod(dst, 0o644)
        except OSError:
            pass


def _fsync_dir(path: str) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def with_suppress(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _fuzzy_replace(text: str, old: str, new: str, *, all_: bool = False) -> tuple[str, int]:
    r"""Match ignoring whitespace differences, preserving the surrounding whitespace.

    The whole point is to rescue an agent whose snippet was reflowed or mis-indented;
    a naive `\s*needle\s*` substitution would *delete* the blank lines around the
    match, so the leading/trailing runs are captured and re-emitted verbatim.
    """
    if not old.strip():
        return text, 0
    old_norm = re.sub(r"\s+", " ", old).strip()
    if old_norm not in re.sub(r"\s+", " ", text):
        return text, 0
    esc = re.escape(old_norm).replace(r"\ ", r"\s+")
    rx = re.compile(r"(?P<pre>\s*)" + esc + r"(?P<post>\s*)")
    matches = list(rx.finditer(text))
    if not matches:
        return text, 0
    body = new            # whitespace is preserved verbatim; the anchors carry it
    if all_:
        out = rx.sub(lambda m: m.group("pre") + body.rstrip("\n") + "\n" + m.group("post"), text)
        return out, len(matches)
    m = matches[0]
    pre, post = m.group("pre"), m.group("post")
    repl = body.rstrip("\n")
    return text[:m.start()] + pre + repl + post + text[m.end():], 1


def _replace_nth(text: str, old: str, new: str, n: int) -> str:
    seen = 0
    idx = -1
    while True:
        idx = text.find(old, idx + 1)
        if idx < 0:
            return text
        seen += 1
        if seen == n:
            return text[:idx] + new + text[idx + len(old):]


def _occurrence_lines(text: str, needle: str, cap: int = 5) -> list[int]:
    """1-based line numbers where `needle` starts, up to `cap`.

    An agent that has to widen an anchor should not have to re-read the whole file to
    learn where the duplicates are; these numbers are enough to pick a neighbour line.
    """
    out: list[int] = []
    start = 0
    while len(out) < cap:
        idx = text.find(needle, start)
        if idx < 0:
            break
        out.append(text.count("\n", 0, idx) + 1)
        start = idx + max(1, len(needle))
    return out


def _nearest_lines(text: str, needle: str, *, window: int = 3) -> list[str]:
    """Show the agent what is actually there, near a shared token."""
    lines = text.splitlines()
    probe = re.sub(r"\s+", " ", needle).strip().split(" ")[:4]
    best, best_hits = -1, 0
    for i, ln in enumerate(lines):
        low = ln.lower()
        hits = sum(1 for p in probe if p and p.lower() in low)
        if hits > best_hits:
            best, best_hits = i, hits
    if best < 0:
        return []
    lo, hi = max(0, best - 1), min(len(lines), best + window + 1)
    return [f"{n + 1:5}| {lines[n]}" for n in range(lo, hi)]


def unified_diff(before: str, after: str, name: str, *, max_lines: int = 400) -> str:
    import difflib

    a = before.replace("\r\n", "\n").splitlines(keepends=True)
    b = after.replace("\r\n", "\n").splitlines(keepends=True)
    text = "".join(difflib.unified_diff(a, b, fromfile=f"a/{name}", tofile=f"b/{name}", n=3))
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n...[{len(lines) - max_lines} diff lines omitted]"
    return text


def apply_hunks(text: str, hunks: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Utility for structured edits from other tools (fs.patch built on top)."""
    errors: list[str] = []
    lines = text.split("\n")
    for h in hunks:
        start = int(h.get("start_line", 1)) - 1
        delete = int(h.get("delete_count", 0))
        insert = list((h.get("insert") or "").split("\n")) if h.get("insert") else []
        if start < 0 or start > len(lines):
            errors.append(f"hunk start {start + 1} out of range (file has {len(lines)} lines)")
            continue
        lines[start:start + delete] = insert
    return "\n".join(lines), errors


def walk_files(root: str, *, sandbox: PathSandbox, limit: int = 200_000) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not sandbox.should_ignore(
            os.path.join("" if rel_dir == "." else rel_dir, d))]
        for fn in filenames:
            yield os.path.join(dirpath, fn)
            limit -= 1
            if limit <= 0:
                return
