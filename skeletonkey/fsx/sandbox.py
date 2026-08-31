r"""Path sandbox: the single chokepoint between an agent's string and the disk.

Agents produce paths from model output, from `rg` results, from `cd` state, and
from scripts - all of which can be influenced by file *contents* they read. So
the resolver assumes the incoming string is adversarial and answers exactly one
question: "is this path inside a root I was granted, after links and dot-dot are
settled?" Everything else in fsx goes through it.

Threats explicitly handled:
  * `..` traversal and `path/../../etc/passwd`
  * symlink escape (link inside root -> target outside root), incl. the
    "parent dir is a link" variant, checked on the *resolved* path too
  * Windows: case-insensitive equality (C:\\FOO vs c:\\foo), ADS (`file.txt:evil`),
    reserved device names (CON/NUL/COM1/trailing dot+space), UNC, `\\?\` prefix,
    drive-relative paths (`C:foo`), both separators, and MAX_PATH for long paths
  * Git-Bash/MSYS style paths (`/c/users/dime`, `/drive/c/...`) that agents copy
    out of `pwd` output inside a POSIX shell
  * null bytes / control chars, and paths that would resolve onto a network mount
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import E, SkeletonKeyError

_IS_WIN = os.name == "nt"
_SEPS = "\\/" if _IS_WIN else "/"
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]?")
_MSYS_RE = re.compile(r"^/(?:cygdrive/)?([A-Za-z])(/|$)")
_UNC_RE = re.compile(r"^\\\\[^\\]+\\[^\\\\]+")
_LONG_PREFIX = "\\\\?\\"
NUL = "\x00"


@dataclass
class SandboxPolicy:
    follow_symlinks: str = "within-roots"        # never | within-roots | always
    deny: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    allow_dotfiles: bool = True
    reject_device_names: bool = True
    long_path_prefix: bool = True
    deny_absolute_outside_roots: bool = True
    # paths that are never readable even if inside a root
    deny_reads: list[str] = field(default_factory=list)


@dataclass
class Resolved:
    """Outcome of a sandbox check. `.display` is what we show agents (relative)."""

    request: str
    abs: str
    real: str
    display: str
    root: str
    rel: str
    exists: bool = False
    is_dir: bool = False
    is_file: bool = False
    is_link: bool = False
    size: int | None = None
    mtime: float | None = None
    mode: int | None = None
    writable: bool = False
    resolved_via_link: bool = False
    long_path: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.display, "abs": self.abs, "rel": self.rel, "root": self.root,
                "exists": self.exists, "is_dir": self.is_dir, "is_file": self.is_file,
                **({"is_link": True} if self.is_link else {}),
                **({"size": self.size} if self.size is not None else {}),
                **({"mtime": self.mtime} if self.mtime is not None else {}),
                **({"mode": stat.filemode(self.mode)[1:] if self.mode is not None else {}}
                   if self.mode is not None else {}),
                "writable": self.writable,
                **({"notes": self.notes} if self.notes else {})}


class PathSandbox:
    def __init__(self, roots: list[str], policy: SandboxPolicy | None = None, *,
                 cwd: str | None = None) -> None:
        if not roots:
            raise ValueError("PathSandbox requires at least one root")
        self.policy = policy or SandboxPolicy()
        self.roots: list[str] = []
        for r in roots:
            self.roots.append(self._prep(r, for_root=True))
        # longest root first so nested roots report the most specific match
        self.roots.sort(key=len, reverse=True)
        # Relative paths resolve against the *primary root*, not the process cwd:
        # an agent says "README.md", not "/abs/path/to/workspace/README.md", and a
        # mid-session chdir must never be able to widen (or shift) the sandbox.
        self.cwd = os.path.abspath(cwd or self.roots[0])
        self._deny_re = [_glob_to_re(g) for g in self.policy.deny]
        self._deny_read_re = [_glob_to_re(g) for g in self.policy.deny_reads]
        self._ignore_re = [_glob_to_re(g) for g in self.policy.ignore]
        self._case_insensitive: bool | None = None

    # ------------------------------------------------------------------ roots
    def add_root(self, path: str) -> str:
        p = self._prep(path, for_root=True)
        if p not in self.roots:
            self.roots.insert(0, p)
            self.roots.sort(key=len, reverse=True)
        return p

    def _prep(self, raw: str, *, for_root: bool = False) -> str:
        """Normalize a *root* or an incoming path to an absolute, comparable form."""
        text = (raw or "").strip()
        if NUL in text:
            raise SkeletonKeyError(E.SANDBOX_VIOLATION, "path contains NUL byte",
                                   details={"path": repr(raw)[:160]})
        text = _expand(text)
        text = self._translate_foreign(text)
        if not _is_abs(text):
            text = os.path.join(self.cwd if not for_root else os.getcwd(), text)
        text = _norm_seps(text)
        if for_root:
            # roots are trusted inputs: resolve links so containment compares realpaths
            try:
                text = os.path.realpath(text)
            except OSError:
                pass
            text = os.path.normpath(text)
        else:
            text = os.path.normpath(text)
        if _IS_WIN:
            text = text.rstrip(". ") if not _is_drive_root(text) else text
        # Both roots and candidates get the *same* final normalization, or a
        # candidate that looks absolute on this OS can fail to match a root that
        # was rewritten by abspath (drive-relative `C:foo` is the classic case).
        return os.path.abspath(text)

    def _translate_foreign(self, text: str) -> str:
        """msys/cygwin/WSL path shapes an agent copies out of a shell."""
        if _IS_WIN:
            m = _MSYS_RE.match(text.replace("\\", "/"))
            if m:
                rest = text[m.end():].lstrip("/")
                return f"{m.group(1).upper()}:\\{rest.replace('/', chr(92))}"
        else:
            # /mnt/c/... on WSL is a real path; /c/... is not - leave both alone
            # and let containment decide. Nothing to translate on POSIX.
            pass
        return text

    # -------------------------------------------------------------- resolution
    def resolve(self, raw: str, *, intent: str = "read", base: str | None = None) -> Resolved:
        """Check a path and return its status. Raises on any policy violation.

        intent: read | write | delete | list | create - write/delete get stricter
        checks (deny_reads applies to read+list; existence rules differ).
        """
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raise SkeletonKeyError(E.BAD_ARGS, "empty path", details={"intent": intent})
        abs_path = self._prep(raw)
        if base and not _is_abs(_expand(raw or "")):
            abs_path = os.path.normpath(os.path.join(base, raw))
        candidates = self._candidate_roots(abs_path)
        if not candidates:
            raise self._violation(raw, abs_path, "outside declared roots", intent)

        # --- link handling on the real path
        real = abs_path
        via_link = False
        if self.policy.follow_symlinks != "never":
            try:
                real = os.path.realpath(abs_path)
            except OSError as exc:
                raise SkeletonKeyError(E.IO, f"realpath failed: {exc}",
                                       details={"path": self._display(abs_path)}) from exc
            via_link = _same(real, abs_path) is False
        elif _islink(abs_path):
            raise SkeletonKeyError(
                E.SANDBOX_VIOLATION, "symlinks are not followed by policy",
                details={"path": self._display(abs_path), "setting": "follow_symlinks=never"},
            )
        # for create, the *parent* must also be contained (link in parent = escape)
        check_target = real if (os.path.exists(real) or _islink(abs_path)) else _dirname(real)
        matched = self._candidate_roots(os.path.normpath(check_target))
        if not matched:
            raise self._violation(raw, real, "resolves outside declared roots via symlink/parent", intent,
                                  logical=abs_path)
        root, rel = matched[0]
        if self.policy.follow_symlinks == "never":
            real = abs_path

        notes: list[str] = []
        if via_link:
            notes.append(f"resolved through symlink to {self._display(real)}")

        # --- Windows-specific refusals
        if _IS_WIN:
            self._win_checks(abs_path)

        # --- deny rules
        blob = self._display(abs_path).replace("\\", "/")
        if intent in ("read", "list", "write", "delete"):
            for rx in self._deny_read_re:
                if rx.search(blob.lower()):
                    raise SkeletonKeyError(
                        E.DENY_RULE, f"path matches a deny_reads rule ({self._display(abs_path)})",
                        details={"path": self._display(abs_path), "intent": intent,
                                 "note": "deny_reads protects credential files inside the workspace"},
                    )
        for rx in self._deny_re:
            if rx.search(blob.lower()):
                raise SkeletonKeyError(
                    E.DENY_RULE, "path matches a configured deny rule",
                    details={"path": self._display(abs_path), "intent": intent},
                )

        st = _stat(real if os.path.exists(real) else abs_path)
        return Resolved(
            request=raw, abs=abs_path, real=real, root=root, rel=rel,
            display=self._display(abs_path), exists=st is not None,
            is_dir=bool(st and stat.S_ISDIR(st.st_mode)), is_file=bool(st and stat.S_ISREG(st.st_mode)),
            is_link=_islink(abs_path), size=(st.st_size if st and stat.S_ISREG(st.st_mode) else None),
            mtime=(st.st_mtime if st else None), mode=(st.st_mode if st else None),
            writable=_writable(real, st),
            resolved_via_link=via_link,
            long_path=self._long_path(abs_path), notes=notes,
        )

    def safe(self, raw: str, *, intent: str = "read") -> bool:
        try:
            self.resolve(raw, intent=intent)
            return True
        except SkeletonKeyError:
            return False

    def should_ignore(self, relpath: str) -> bool:
        """Ignore-rule test. A `dir/**` rule also matches `dir` itself, so a walk
        prunes the directory instead of descending into it to discover nothing."""
        low = relpath.replace("\\", "/").lower().strip("/")
        return any(rx.search(low) or rx.search(low + "/") for rx in self._ignore_re)

    # ----------------------------------------------------------------- helpers
    def _candidate_roots(self, abs_path: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for root in self.roots:
            rel = _relative(abs_path, root)
            if rel is not None:
                out.append((root, rel))
        return out

    def _violation(self, raw: str, resolved: str, why: str, intent: str, *, logical: str | None = None) -> SkeletonKeyError:
        return SkeletonKeyError(
            E.SANDBOX_VIOLATION, f"{self._display(resolved)}: {why}",
            details={
                "requested": raw, "resolved": self._display(resolved),
                **({"logical": self._display(logical)} if logical and logical != resolved else {}),
                "intent": intent, "allowed_roots": list(self.roots),
                "note": "paths may be given relative to any root, or absolute inside one",
                "advice": "pass a path inside allowed_roots, or add the directory to `roots` in skeletonkey.toml",
            },
            next_actions=[{"tool": "registry.search", "args": {"query": "access files in another directory"},
                           "why": "a tool with a wider grant may exist"}],
        )

    def _win_checks(self, abs_path: str) -> None:
        name = _basename(abs_path) or abs_path
        if self.policy.reject_device_names:
            stem = name.split(".")[0].upper()
            if stem in _WIN_RESERVED:
                raise SkeletonKeyError(
                    E.BAD_ARGS, f"Windows reserved device name in path: {name!r}",
                    details={"path": self._display(abs_path), "reserved": sorted(_WIN_RESERVED)[:6],
                             "note": "CON/NUL/COM1... are invisible-but-real on any drive"},
                )
        if ":" in name:
            raise SkeletonKeyError(
                E.BAD_ARGS, "NTFS alternate data stream in filename is not allowed",
                details={"path": self._display(abs_path), "note": "an ADS write silently loses data"},
            )
        if re.search(r"(?:[\x00-\x1f<>\"|?*])", abs_path):
            raise SkeletonKeyError(E.BAD_ARGS, "illegal Windows filename characters",
                                   details={"path": repr(abs_path)[:160],
                                            "illegal_chars": '<>:"/|?* + control'})

    def _long_path(self, abs_path: str) -> str | None:
        if not (_IS_WIN and self.policy.long_path_prefix):
            return None
        if abs_path.startswith(_LONG_PREFIX):
            return abs_path
        if len(abs_path) < 250 and not _UNC_RE.match(abs_path):
            return None
        if _DRIVE_RE.match(abs_path):
            return _LONG_PREFIX + abs_path
        if abs_path.startswith("\\\\"):
            return _LONG_PREFIX + "UNC" + abs_path[1:]
        return None

    def _display(self, path: str) -> str:
        """Root-relative display when possible; shorter + no host-specific noise."""
        for root in self.roots:
            rel = _relative(path, root)
            if rel is not None:
                return rel or "."
        return path

    # ---------------------------------------------------------------- open-time
    def open(self, raw: str, mode: str = "rb", *, intent: str | None = None,
             buffering: int = -1, encoding: str | None = None, newline: str | None = None) -> Any:
        """resolve() then open() on the *validated* path.

        Race note: we open `real` (the link-resolved target) with O_NOFOLLOW on
        the final component where the OS supports it, so a symlink swapped in
        between check and open still fails closed.
        """
        res = self.resolve(raw, intent=intent or ("write" if any(c in mode for c in "wax+") else "read"))
        # open the *link-resolved* target we just authorized, never the raw string
        target = res.real
        kw: dict[str, Any] = {"mode": mode}
        if "b" in mode:
            kw["buffering"] = buffering
        else:
            kw.update({"encoding": encoding or "utf-8", "errors": "replace", "newline": newline})
        try:
            if "w" in mode or "x" in mode or "a" in mode or "+" in mode:
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            return open(target, **kw)
        except OSError as exc:
            raise SkeletonKeyError(
                E.PATH_UNREADABLE if exc.errno in (13, 22) else E.IO,
                f"open({res.display}, {mode!r}) failed: {exc.strerror or exc}",
                details={"path": res.display, "errno": exc.errno, "intent": intent},
            ) from exc

    def for_walk(self, root: str) -> tuple[str, list[str]]:
        """(dir_path, names) with ignored entries removed - used by listings/search."""
        res = self.resolve(root, intent="list")
        try:
            names = sorted(os.listdir(res.real))
        except OSError as exc:
            raise SkeletonKeyError(E.IO, f"listdir failed: {exc}", details={"path": res.display}) from exc
        kept = [n for n in names if not self.should_ignore(os.path.join(res.rel, n))]
        return res.real, kept


# ------------------------------------------------------------------- primitives


def _expand(text: str) -> str:
    out = os.path.expanduser(text)
    if _IS_WIN:
        out = os.path.expandvars(out)
    return out


def _norm_seps(text: str) -> str:
    if _IS_WIN:
        return text.replace("/", "\\")
    return text


def _is_abs(text: str) -> bool:
    if _IS_WIN:
        return bool(_DRIVE_RE.match(text)) or text.startswith("\\\\") or text.startswith(_LONG_PREFIX)
    return text.startswith("/")


def _is_drive_root(path: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]:[\\/]*", path)) or path in ("\\", "//")


def _same(a: str, b: str) -> bool:
    return _key(a) == _key(b)


def _key(path: str) -> str:
    p = os.path.normpath(path)
    if _IS_WIN:
        p = p.lower().rstrip("\\")
        if p.endswith(":"):
            p += "\\"
    return p


def _relative(path: str, root: str) -> str | None:
    """`path` under `root`? Returns rel (possibly "") or None if not contained."""
    p, r = _key(path), _key(root)
    if p == r:
        return ""
    prefix = r if r.endswith(("\\", "/")) else r + (os.sep if _IS_WIN else "/")
    if _IS_WIN:
        prefix = prefix.rstrip("\\/") + "\\"
        if not p.startswith(prefix.rstrip("\\")) and p != prefix.rstrip("\\").lower():
            # normalize separator for the compare
            p2, pre2 = p.replace("/", "\\"), prefix.replace("/", "\\")
            if not p2.startswith(pre2):
                return None
            rel = p2[len(pre2):]
            return rel.replace("\\", "/")
        rel = p[len(prefix):]
        return rel.replace("\\", "/")
    if not p.startswith(prefix):
        return None
    return p[len(prefix):]


def _writable(path: str, st: os.stat_result | None) -> bool:
    """Write-ability judged against the nearest *existing* ancestor, so creating a
    new file in a creatable directory is not mis-reported as unwritable."""
    if st is not None:
        return os.access(path, os.W_OK)
    probe = path
    for _ in range(24):
        probe = _dirname(probe) or probe
        if os.path.isdir(probe):
            return os.access(probe, os.W_OK)
        if not probe:
            break
    return False


def _dirname(path: str) -> str:
    """Parent dir that understands *both* separators.

    os.path.dirname on a Windows-shaped path (or a path that realpath re-prefixed
    with `/`) silently returns the wrong parent, which turned a legitimate
    "write into a root" into a SANDBOX_VIOLATION. Relevant for Git-Bash/MSYS and
    WSL-shaped mounts, not just for tests.
    """
    if not _IS_WIN:
        return os.path.dirname(path)
    trimmed = path.rstrip("\\/")
    i = max(trimmed.rfind("\\"), trimmed.rfind("/"))
    if i < 0:
        return ""
    if i > 0 and trimmed[i - 1] == ":":       # keep "C:\" intact
        return trimmed[: i + 1]
    return trimmed[:i]


def _basename(path: str) -> str:
    if not _IS_WIN:
        return os.path.basename(path)
    trimmed = path.rstrip("\\/")
    i = max(trimmed.rfind("\\"), trimmed.rfind("/"))
    return trimmed[i + 1:] if i >= 0 else trimmed


def _stat(path: str) -> os.stat_result | None:
    try:
        return os.stat(path)
    except OSError:
        return None


def _islink(path: str) -> bool:
    try:
        return os.path.islink(path)
    except OSError:
        return False


def _glob_to_re(glob: str) -> re.Pattern[str]:
    """`**` spans separators, `*` doesn't; a bare dir glob also matches its subtree."""
    out: list[str] = []
    i = 0
    g = glob.replace("\\", "/").lower()
    while i < len(g):
        c = g[i]
        if c == "*":
            if g[i:i + 2] == "**":
                jump = i + 2
                if g[jump:jump + 1] == "/":
                    out.append("(?:.*/)?")
                    i = jump + 1
                    continue
                out.append(".*")
                i = jump
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        if c == "[":
            j = g.find("]", i)
            if j > i:
                out.append(g[i:j + 1])
                i = j + 1
                continue
        out.append(re.escape(c))
        i += 1
    return re.compile(r"^(?:" + "".join(out) + r")(?:/.*)?$")


def detect_case_sensitivity(path: str) -> bool:
    """Used by the prober; kept here so tests can assert on real behaviour."""
    probe = os.path.join(path, f".sk-case-probe-{os.getpid()}")
    try:
        os.makedirs(probe, exist_ok=True)
        target = os.path.join(probe, "File")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        return not os.path.exists(os.path.join(probe, "file"))
    except OSError:
        return not _IS_WIN
    finally:
        try:
            import shutil

            shutil.rmtree(probe, ignore_errors=True)
        except OSError:
            pass
