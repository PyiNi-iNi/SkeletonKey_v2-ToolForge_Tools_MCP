"""Text search with adaptive provider selection.

`rg` when present (it is 10-50x faster and honours .gitignore), a pure-Python
walker otherwise. Same normalized result shape either way, because the agent
must not have to care which provider answered - and the `provider` field tells
it, so a `rg`-specific flag request can be refused with an explanation instead
of silently misbehaving.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import E, SkeletonKeyError
from ..core.profile import CapabilityProfile
from ..core.util import is_windows
from .sandbox import PathSandbox, _glob_to_re


@dataclass
class SearchHit:
    path: str
    line: int
    text: str
    col: int | None = None
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {"path": self.path, "line": self.line, "text": self.text}
        if self.col:
            d["col"] = self.col
        if self.before:
            d["before"] = self.before
        if self.after:
            d["after"] = self.after
        return d


@dataclass
class SearchOutcome:
    provider: str
    pattern: str
    hits: list[SearchHit] = field(default_factory=list)
    files_matched: int = 0
    total_matches: int = 0
    truncated: bool = False
    duration_ms: int = 0
    scanned_files: int = 0
    notes: list[str] = field(default_factory=list)
    via: dict[str, Any] | None = None

    def to_dict(self, *, max_hits: int = 200) -> dict[str, Any]:
        shown = self.hits[:max_hits]
        return {"provider": self.provider, "pattern": self.pattern, "mode": "matches",
                "matches": [h.to_dict() for h in shown], "count": len(shown),
                "total_matches": self.total_matches, "files_matched": self.files_matched,
                "truncated": self.truncated or len(self.hits) > max_hits,
                "scanned_files": self.scanned_files, "duration_ms": self.duration_ms,
                "next": ({"tool": "fs.search", "args": {"pattern": self.pattern, "limit": max_hits * 2}}
                         if len(self.hits) > max_hits else None),
                **({"via": self.via} if self.via else {}),
                **({"notes": self.notes} if self.notes else {})}


class SearchBackend:
    def __init__(self, sandbox: PathSandbox, profile: CapabilityProfile | None = None,
                 *, prefer: str | None = None) -> None:
        self.sb = sandbox
        self.profile = profile
        self.prefer = prefer

    def provider(self) -> str:
        if self.prefer in ("python", "grep", "ripgrep"):
            if self.prefer == "grep":
                # no separate grep backend: the built-in walker is grep-compatible
                # for everything this tool exposes, and it is honest about its name.
                return "python"
            if self.prefer == "ripgrep" and not (self.profile and "search.ripgrep" in self.profile.capabilities):
                raise SkeletonKeyError(
                    E.MISSING_BINARY, "prefer='ripgrep' but rg is not installed",
                    details={"missing": "rg", "fallback": "python"},
                    next_actions=[{"tool": "fs.search", "args": {"prefer": "python", "pattern": "..."}}],
                )
            return {"python": "python", "grep": "grep", "ripgrep": "ripgrep"}[self.prefer]
        if self.profile and "search.ripgrep" in self.profile.capabilities:
            return "ripgrep"
        return "python"

    def search(self, pattern: str, *, path: str = ".", regex: bool = False, ignore_case: bool = False,
               fixed: bool = False, word: bool = False, context: int = 0, glob: str | None = None,
               type_: str | None = None, limit: int = 400, max_bytes: int = 2_000_000,
               multiline: bool = False, files_with_matches: bool = False,
               timeout_s: float = 60.0) -> SearchOutcome:
        t0 = time.monotonic()
        root = self.sb.resolve(path, intent="list")
        prov = self.provider()
        if prov == "ripgrep":
            out = self._rg(pattern, root=root, regex=regex, ignore_case=ignore_case, fixed=fixed, word=word,
                           context=context, glob=glob, type_=type_, limit=limit, multiline=multiline,
                           files_with_matches=files_with_matches, timeout_s=timeout_s)
        else:
            out = self._python(pattern, root=root, regex=regex, ignore_case=ignore_case, fixed=fixed,
                               word=word, context=context, glob=glob, limit=limit, max_bytes=max_bytes,
                               multiline=multiline, files_with_matches=files_with_matches)
        out.duration_ms = int((time.monotonic() - t0) * 1000)
        out.via = root.via()
        return out

    # ------------------------------------------------------------------ ripgrep
    def _rg(self, pattern: str, *, root: Any, regex: bool, ignore_case: bool, fixed: bool, word: bool,
            context: int, glob: str | None, type_: str | None, limit: int, multiline: bool,
            files_with_matches: bool, timeout_s: float) -> SearchOutcome:
        rg = self.profile.has_binary("rg") if self.profile else None
        if not rg:
            raise SkeletonKeyError(E.MISSING_BINARY, "ripgrep disappeared between probe and call",
                                   details={"missing": "rg", "advice": "re-run profile.probe"})
        # -n/-H/--null are requested explicitly: a host ~/.ripgreprc (or an older rg) can
        # otherwise drop the line numbers, and NUL-separating the path keeps Windows paths
        # like C:\work\a.py:42: parsing correctly.
        argv = [rg, "--no-heading", "--with-filename", "--line-number", "--null",
                "--color", "never", "--max-columns", "400", "--max-columns-preview",
                "--sort", "path"]
        if not regex:
            argv.append("--fixed-strings")
        if fixed:
            argv.append("--fixed-strings")
        if ignore_case:
            argv.append("--ignore-case")
        if word:
            argv.append("--word-regexp")
        if multiline:
            argv.append("--multiline")
        if glob:
            argv += ["--glob", glob]
        if type_:
            argv += ["--type", type_]
        if files_with_matches:
            argv += ["--files-with-matches"]
        argv += ["--max-filesize", "5M", "--", pattern, root.real]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout_s, check=False, stdin=subprocess.DEVNULL,
                                  creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_windows() else 0))
        except subprocess.TimeoutExpired:
            raise SkeletonKeyError(E.TIMEOUT, f"ripgrep exceeded {timeout_s}s",
                                   details={"pattern": pattern[:120], "root": root.display,
                                            "advice": "narrow glob/type or lower context"}) from None
        except OSError as exc:
            raise SkeletonKeyError(E.IO, f"could not run rg: {exc}", details={"path": rg}) from exc
        if proc.returncode not in (0, 1, 2):
            raise SkeletonKeyError(E.NONZERO_EXIT, f"rg failed: {(proc.stderr or '').strip()[:300]}",
                                   details={"exit_code": proc.returncode, "stderr": proc.stderr[:500]})
        raw_out = proc.stdout or ""
        if files_with_matches:
            # `-l --null` separates entries with NUL, not newlines
            lines = [ln for ln in re.split(r"[\x00\n]", raw_out) if ln.strip()]
        else:
            lines = raw_out.splitlines()
        hits: list[SearchHit] = []
        total = 0
        for line in lines:
            if files_with_matches:
                try:
                    rel = self.sb.resolve(line.strip(), intent="read").display
                except SkeletonKeyError:
                    continue
                total += 1
                if len(hits) < limit:
                    hits.append(SearchHit(path=rel, line=0, text=""))
                continue
            file_s, line_s, text_s = _split_rg_line(line)
            if file_s is None:
                continue
            total += 1
            try:
                rel = self.sb.resolve(file_s, intent="read").display
            except SkeletonKeyError:
                continue
            if len(hits) < limit:
                hits.append(SearchHit(path=rel, line=line_s, text=text_s[:400]))
        if not hits and proc.returncode == 0 and (proc.stdout or "").strip():
            notes_ = ["rg produced output that could not be parsed; results are reported empty "
                      "rather than guessed"]
        else:
            notes_ = []
        if context and hits:
            _attach_context(hits, int(context), self.sb)
        notes = notes_
        if proc.stderr and proc.stderr.strip():
            notes.append(f"rg stderr: {proc.stderr.strip()[:200]}")
        if proc.returncode == 2:
            notes.append("rg reported a pattern/flag problem (exit 2); results may be partial")
        return SearchOutcome(provider="ripgrep", pattern=pattern, hits=hits, total_matches=total,
                             files_matched=len({h.path for h in hits}), truncated=total > len(hits),
                             notes=notes)

    # ------------------------------------------------------------------- python
    def _python(self, pattern: str, *, root: Any, regex: bool, ignore_case: bool, fixed: bool, word: bool,
                context: int, glob: str | None, limit: int, max_bytes: int, multiline: bool,
                files_with_matches: bool) -> SearchOutcome:
        flags = re.IGNORECASE if ignore_case else 0
        if regex and not fixed:
            try:
                rx = re.compile(pattern, flags | (re.DOTALL | re.MULTILINE if multiline else 0))
            except re.error as exc:
                raise SkeletonKeyError(
                    E.BAD_ARGS, f"invalid regex: {exc}",
                    details={"pattern": pattern[:200], "error": str(exc),
                             "advice": "set regex=false for literal text, or pass a valid pattern"},
                ) from exc
        else:
            lit = re.escape(pattern)
            if word:
                lit = r"\b" + lit + r"\b"
            rx = re.compile(lit, flags)
        grx = _glob_to_re(glob) if glob else None
        notes: list[str] = ["provider=python: no .gitignore semantics, use ignore_config=true with rg for those"]
        hits: list[SearchHit] = []
        total = 0
        scanned = 0
        truncated = False
        seen_files: set[str] = set()
        base = root.real
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and not self.sb.should_ignore(_join(rel_dir, d))]
            for fn in sorted(filenames):
                rel = _join(rel_dir, fn)
                if self.sb.should_ignore(rel) or (grx and not grx.search(rel.lower())):
                    continue
                abs_child = os.path.join(dirpath, fn)
                scanned += 1
                if scanned > 60_000:
                    truncated = True
                    notes.append("stopped after 60k files; narrow the path or use rg")
                    break
                try:
                    if os.path.getsize(abs_child) > max_bytes:
                        notes.append(f"skipped oversized {rel} (> {max_bytes}B)")
                        continue
                    with open(abs_child, "rb") as fh:
                        raw = fh.read(max_bytes)
                except OSError:
                    continue
                if b"\x00" in raw[:4096]:
                    continue  # binary
                text = raw.decode("utf-8", "replace")
                lines = text.split("\n")
                if multiline:
                    for m in rx.finditer(text):
                        total += 1
                        if len(hits) >= limit:
                            truncated = True
                            continue
                        ln = text.count("\n", 0, m.start()) + 1
                        hits.append(SearchHit(path=rel, line=ln, text=m.group(0)[:400]))
                        seen_files.add(rel)
                    continue
                for i, line in enumerate(lines):
                    if not rx.search(line):
                        continue
                    total += 1
                    if files_with_matches:
                        seen_files.add(rel)
                        continue
                    if len(hits) >= limit:
                        truncated = True
                        continue
                    col = None
                    m0 = rx.search(line)
                    if m0:
                        col = m0.start() + 1
                    hits.append(SearchHit(
                        path=rel, line=i + 1, text=line[:400], col=col,
                        before=lines[max(0, i - context):i] if context else [],
                        after=lines[i + 1:i + 1 + context] if context else []))
                    seen_files.add(rel)
            if truncated:
                break
        if files_with_matches:
            return SearchOutcome(provider="python", pattern=pattern,
                                 hits=[SearchHit(path=p, line=0, text="") for p in sorted(seen_files)],
                                 files_matched=len(seen_files), total_matches=total, truncated=truncated,
                                 scanned_files=scanned, notes=notes)
        return SearchOutcome(provider="python", pattern=pattern, hits=hits, total_matches=total,
                             files_matched=len(seen_files), truncated=truncated, scanned_files=scanned,
                             notes=notes)


def _split_rg_line(line: str) -> tuple[str | None, int, str]:
    """`path\x00line:text` (rg --null -n), falling back to `path:line:text`.

    Returns (None, 0, "") for anything that is not a match line (group separators,
    context lines when --null is unavailable). A missing line number yields 0 -
    better an unlocated hit than a silently dropped one.
    """
    file_s, sep, rest = line.partition("\x00")
    if sep:
        num, div, text = rest.partition(":")
        if div and num.isdigit():
            return file_s, int(num), text
        if div and num.startswith("-"):          # context/group noise
            return None, 0, ""
        return file_s, 0, rest
    m = re.match(r"^(?P<file>.*?):(?P<line>\d+)?[:]?(?P<text>.*)$", line)
    if not m or (not m.group("text") and not m.group("line")):
        return None, 0, ""
    if line.startswith("--"):
        return None, 0, ""
    return (m.group("file"), int(m.group("line") or 0), m.group("text"))


def _attach_context(hits: list[SearchHit], context: int, sb: PathSandbox) -> None:
    """Fill before/after from the files themselves.

    rg's own -C output interleaves context with matches in a format that is easy to
    mis-read (and impossible to tell apart when a path contains a colon), so the
    provider returns matches only and the surrounding lines are read back directly.
    """
    cache: dict[str, list[str] | None] = {}
    for h in hits:
        if h.path not in cache:
            lines: list[str] | None = None
            try:
                real = sb.resolve(h.path, intent="read").real
                if os.path.getsize(real) <= 2_000_000:
                    with open(real, encoding="utf-8", errors="replace") as fh:
                        lines = [ln.rstrip("\n") for ln in fh]
            except (SkeletonKeyError, OSError):
                lines = None
            cache[h.path] = lines
        lines = cache[h.path]
        if not lines or h.line <= 0 or h.line > len(lines):
            continue
        h.before = lines[max(0, h.line - 1 - context):h.line - 1]
        h.after = lines[h.line:h.line + context]


def _join(a: str, b: str) -> str:
    if not a or a == ".":
        return b
    return f"{a}/{b}"
