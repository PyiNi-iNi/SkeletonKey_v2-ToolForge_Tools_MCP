"""CapabilityProfile - what this specific machine can actually do.

Everything adaptive in SkeletonKey hangs off this object: which tools get
advertised, which provider wins for a capability, which shell dialect to render,
how to encode text, and what to warn about. It is probed once, fingerprinted,
cached to disk, and re-probed when the fingerprint (PATH, OS, key versions)
changes or `ttl` expires.

Results are *receipted*: `probe_receipt` records how each fact was learned, so
an agent told "pwsh unavailable" can be shown the command we tried.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from .util import env_fingerprint, is_windows, short_hash

PROBE_TIMEOUT = 8.0
CURRENT_SCHEMA = 3
UNPROBED = "probing disabled"

# version-sensitive tools worth a spawn (each costs ~10ms; cached in profile)
VERSIONED_BINARIES = ("rg", "git", "jq", "7z", "node", "python3", "tar", "grep")


def _parse_ver(text: str) -> tuple[int, ...]:
    """Extract a comparable version tuple from noisy `--version` output."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        m2 = re.search(r"(\d+)", text or "")
        return (int(m2.group(1)),) if m2 else (0,)
    return tuple(int(x) if x else 0 for x in m.groups(0))  # type: ignore[misc]


def version_tuple(text: str) -> tuple[int, ...]:
    return _parse_ver(text)


def version_gte(have: tuple[int, ...], want: str) -> bool:
    """True if `have` >= the dotted version in `want` (padding shorter tuples)."""
    wanted = _parse_ver(want)
    n = max(len(have), len(wanted))
    padded_a = tuple(have) + (0,) * (n - len(have))
    padded_b = tuple(wanted) + (0,) * (n - len(wanted))
    return padded_a >= padded_b


@dataclass
class ShellProbe:
    """One discovered shell interpreter, with the facts tools key off."""

    dialect: str                       # bash | sh | zsh | pwsh | powershell | python
    kind: str                          # "unix" | "powershell" | "python"
    path: str
    version: tuple[int, ...] = (0,)
    version_text: str = ""
    usable: bool = True
    notes: list[str] = field(default_factory=list)
    # capabilities inferred from dialect+version
    supports_native_error_action: bool = False   # PS >= 7.3 $PSNativeCommandUseErrorActionPreference
    supports_pipefail: bool = False
    supports_stdin_command: bool = False         # `-Command -` on stdin (PS7+ only)
    utf8_default: bool = False

    @property
    def major(self) -> int:
        return self.version[0] if self.version else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dialect": self.dialect, "kind": self.kind, "path": self.path,
            "version": ".".join(str(v) for v in self.version), "usable": self.usable,
            "notes": self.notes,
            "caps": {
                "native_error_action": self.supports_native_error_action,
                "pipefail": self.supports_pipefail,
                "stdin_command": self.supports_stdin_command,
                "utf8_default": self.utf8_default,
            },
        }


@dataclass
class ProbeReceipt:
    command: str
    rc: int | None
    duration_ms: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command, "rc": self.rc, "duration_ms": self.duration_ms,
                **({"detail": self.detail} if self.detail else {})}


@dataclass
class CapabilityProfile:
    """Immutable-ish snapshot of host capabilities."""

    os: str = ""                       # "windows" | "linux" | "darwin" | ...
    os_release: str = ""
    arch: str = ""
    python_version: str = ""
    is_admin: bool = False
    shells: dict[str, ShellProbe] = field(default_factory=dict)
    binaries: dict[str, str] = field(default_factory=dict)     # name -> resolved path
    python_modules: dict[str, bool] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)          # whitelisted, redacted
    filesystem: dict[str, Any] = field(default_factory=dict)
    console: dict[str, Any] = field(default_factory=dict)
    capabilities: set[str] = field(default_factory=set)        # "search.ripgrep", "exec.sandbox", ...
    versions: dict[str, str] = field(default_factory=dict)     # binary name -> version string
    probe_receipt: list[ProbeReceipt] = field(default_factory=list)
    fingerprint: str = ""
    probed_at: float = 0.0
    schema: int = CURRENT_SCHEMA
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- lookups
    def has_binary(self, name: str) -> str | None:
        return self.binaries.get(name.lower())

    def shell(self, dialect: str) -> ShellProbe | None:
        return self.shells.get(dialect)

    def available_dialects(self) -> list[str]:
        return sorted(d for d, s in self.shells.items() if s.usable)

    def preferred_dialect(self) -> str:
        """The safest default for 'just run this'."""
        for cand in ("bash", "zsh", "sh") if not is_windows() else ("pwsh", "powershell"):
            if cand in self.shells and self.shells[cand].usable:
                return cand
        if "pwsh" in self.shells:
            return "pwsh"
        return "python" if "python" in self.shells else (self.available_dialects()[0] if self.shells else "python")

    def meets_any(self, requirements: list[Any]) -> tuple[bool, list[str]]:
        """True if at least one requirement holds. Returns (ok, human-readable notes)."""
        notes: list[str] = []
        for req in requirements:
            ok, unmet = self.meets([req])
            if ok:
                return True, []
            notes.extend(unmet)
        return False, notes

    def meets(self, requirements: list[Any]) -> tuple[bool, list[str]]:
        """Evaluate Requirement list -> (satisfied, list of unmet human strings)."""
        unmet: list[str] = []
        for req in requirements:
            kind, name = req.kind, req.name
            if kind == "binary":
                if not self.has_binary(name):
                    unmet.append(f"binary {name!r} not on PATH")
                elif req.min_version:
                    found = self.versions.get(name.lower())
                    if found and not version_gte(_parse_ver(found), req.min_version):
                        unmet.append(f"{name} {req.min_version}+ required, found {found}")
                    # version unknown but binary present -> assume satisfied, warn upstream
            elif kind == "shell":
                sp = self.shell(name)
                if not sp or not sp.usable:
                    unmet.append(f"shell dialect {name!r} unavailable")
                elif req.min_version and not version_gte(sp.version, req.min_version):
                    unmet.append(f"{name} {req.min_version}+ required, found {'.'.join(map(str, sp.version))}")
            elif kind == "python":
                if not self.python_modules.get(name, False):
                    unmet.append(f"python module {name!r} not importable")
            elif kind == "env":
                if name not in self.env:
                    unmet.append(f"env var {name!r} unset")
            elif kind == "capability":
                if name not in self.capabilities:
                    unmet.append(f"capability {name!r} not present")
            elif kind == "os":
                if self.os not in [x.strip() for x in name.split("|")]:
                    unmet.append(f"requires os in {name!r}, this is {self.os!r}")
            elif kind == "not_os":
                if self.os in [x.strip() for x in name.split("|")]:
                    unmet.append(f"excluded on os {self.os!r}")
            elif kind == "filesystem":
                if not self.filesystem.get(name, False):
                    unmet.append(f"filesystem feature {name!r} unsupported")
            else:
                unmet.append(f"unknown requirement kind {kind!r}")
        return (not unmet), unmet

    def to_dict(self, *, include_receipts: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "os": self.os, "os_release": self.os_release, "arch": self.arch,
            "python": self.python_version, "is_admin": self.is_admin,
            "shells": {k: v.to_dict() for k, v in sorted(self.shells.items())},
            "binaries": dict(sorted(self.binaries.items())),
            "versions": dict(sorted(self.versions.items())),
            "capabilities": sorted(self.capabilities),
            "filesystem": self.filesystem,
            "console": self.console,
            "fingerprint": self.fingerprint,
            "probed_at": self.probed_at,
            "schema": self.schema,
        }
        if self.warnings:
            out["warnings"] = self.warnings
        if include_receipts:
            out["probe_receipt"] = [r.to_dict() for r in self.probe_receipt]
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CapabilityProfile:
        shells = {}
        for name, s in (raw.get("shells") or {}).items():
            caps = s.get("caps", {})
            shells[name] = ShellProbe(
                dialect=s["dialect"], kind=s["kind"], path=s["path"],
                version=tuple(int(x) for x in str(s.get("version", "0")).split(".") if x.isdigit()),
                usable=s.get("usable", True), notes=list(s.get("notes", [])),
                supports_native_error_action=caps.get("native_error_action", False),
                supports_pipefail=caps.get("pipefail", False),
                supports_stdin_command=caps.get("stdin_command", False),
                utf8_default=caps.get("utf8_default", False),
            )
        return cls(
            os=raw.get("os", ""), os_release=raw.get("os_release", ""), arch=raw.get("arch", ""),
            python_version=raw.get("python", ""), is_admin=raw.get("is_admin", False), shells=shells,
            binaries=dict(raw.get("binaries") or {}), versions=dict(raw.get("versions") or {}),
            python_modules=dict(raw.get("python_modules") or {}),
            env=dict(raw.get("env") or {}), filesystem=dict(raw.get("filesystem") or {}),
            console=dict(raw.get("console") or {}), capabilities=set(raw.get("capabilities") or []),
            fingerprint=raw.get("fingerprint", ""), probed_at=float(raw.get("probed_at") or 0),
            schema=int(raw.get("schema") or 0), warnings=list(raw.get("warnings") or []),
        )


# ---------------------------------------------------------------------- probing

INTERESTING_ENV = ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "HOME", "USERPROFILE", "COMSPEC",
                   "SystemRoot", "TEMP", "TMP", "PWD", "SKELETONKEY_ROOT", "CI", "WSL_DISTRO_NAME")

BINARY_SET = (
    "bash", "sh", "zsh", "fish", "dash", "cmd", "pwsh", "powershell",
    "git", "rg", "grep", "find", "sed", "awk", "jq", "tar", "zip", "unzip", "7z",
    "curl", "wget", "ssh", "scp", "rsync", "docker", "podman", "kubectl",
    "python", "python3", "pip", "pip3", "uv", "node", "npm", "pnpm", "yarn", "bun",
    "go", "cargo", "rustc", "make", "cmake", "gcc", "clang", "msbuild", "dotnet",
    "taskkill", "where", "which", "sort", "uniq", "wc", "head", "tail", "cat",
    "diff", "patch", "tree", "du", "stat", "chmod", "chown", "ln", "readlink",
    "openssl", "sqlite3", "tclsh", "wsl.exe", "schtasks", "reg", "attrib",
    "7za", "ninja", "ctags", "fd", "fdfind", "bat", "delta", "gh", "docker-compose",
    "tar.exe", "robocopy", "Get-ChildItem", "chmod.exe", "icacls",
)


class Prober:
    """Builds a CapabilityProfile. Pure-ish: no writes except the optional cache."""

    def __init__(self, *, env: dict[str, str] | None = None, include: tuple[str, ...] = BINARY_SET,
                 run_probes: bool = True, timeout: float = PROBE_TIMEOUT) -> None:
        self.env = env if env is not None else dict(os.environ)
        self.include = include
        self.run_probes = run_probes
        self.timeout = timeout

    # ---- helpers
    def _run(self, argv: list[str], args: list[str] | None = None) -> tuple[int | None, str, str]:
        if not self.run_probes:
            # Deterministic, spawn-free mode used by tests and by `--no-probe`
            # cold starts: facts degrade to "unknown", never to "broken".
            return 0, "", UNPROBED
        full = [*argv, *(args or [])]
        try:
            proc = subprocess.run(
                full, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout, env=self.env, shell=False, stdin=subprocess.DEVNULL,
                creationflags=_win_flags(),
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, "", f"{type(exc).__name__}: {exc}"

    # ---- sections
    def probe(self, *, roots: list[str] | None = None, cache_path: str | None = None,
              ttl: float = 3600.0, force: bool = False) -> CapabilityProfile:
        prof = CapabilityProfile()
        prof.python_version = platform.python_version()
        prof.os = _os_name()
        prof.os_release = _os_release()
        prof.arch = (platform.machine() or "unknown").lower()
        prof.env = {k: self.env[k] for k in INTERESTING_ENV if k in self.env}
        prof.fingerprint = compute_fingerprint(prof)

        if cache_path and not force:
            cached = load_cached_profile(cache_path, prof.fingerprint, ttl=ttl)
            if cached is not None:
                return cached

        prof.is_admin = self._admin()
        prof.filesystem = self._filesystem(roots or [])
        prof.console = self._console()
        prof.binaries = self._binaries()
        prof.versions = self._binary_versions(prof.binaries) if self.run_probes else {}
        prof.shells = self._shells(prof)
        prof.python_modules = self._modules()
        prof.capabilities = self._capabilities(prof)
        prof.warnings.extend(self._warn(prof))
        prof.fingerprint = compute_fingerprint(prof)
        prof.probed_at = time.time()

        if cache_path:
            save_cached_profile(cache_path, prof)
        return prof

    def _admin(self) -> bool:
        if is_windows():
            try:
                import ctypes

                return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
            except Exception:
                return False
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

    def _filesystem(self, roots: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {"case_sensitive": None, "symlinks": False, "hardlinks": False,
                               "max_component": 255, "fs_type": "unknown"}
        probe_root = roots[0] if roots else (self.env.get("TMP") or self.env.get("TEMP") or sys.prefix)
        base = os.path.join(str(probe_root), f".sk-probe-{short_hash(str(time.time()), 8)}")
        try:
            os.makedirs(base, exist_ok=True)
            a = os.path.join(base, "Case.TXT")
            with open(a, "w", encoding="utf-8") as fh:
                fh.write("x")
            out["case_sensitive"] = not os.path.exists(os.path.join(base, "case.txt"))
            try:
                os.symlink("Case.TXT", os.path.join(base, "link"))
                out["symlinks"] = True
            except (OSError, NotImplementedError):
                out["symlinks"] = False
            try:
                os.link(a, os.path.join(base, "hl"))
                out["hardlinks"] = True
            except (OSError, NotImplementedError, AttributeError):
                out["hardlinks"] = False
            try:
                os.chmod(a, 0o600)
                out["posix_permissions"] = True
            except (OSError, NotImplementedError, AttributeError):
                out["posix_permissions"] = False
            try:
                out["fs_type"] = _fs_type(base) or "unknown"
            except Exception:
                pass
        except OSError as exc:
            out["probe_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                shutil.rmtree(base, ignore_errors=True)
            except OSError:
                pass
        if out["case_sensitive"] is None:
            out["case_sensitive"] = not is_windows()
        out.setdefault("posix_permissions", False)
        return out

    def _console(self) -> dict[str, Any]:
        con: dict[str, Any] = {"tty": sys.stdout.isatty(), "color": False,
                               "preferred_encoding": "utf-8", "code_page": None}
        enc = (self.env.get("PYTHONIOENCODING") or self.env.get("LC_ALL") or self.env.get("LANG") or "")
        m = re.search(r"utf-?8|cp\d+|latin-?1|ascii", enc, re.I)
        if m:
            con["preferred_encoding"] = "utf-8" if "utf" in m.group(0).lower() else m.group(0).lower()
        if is_windows():
            try:
                _, out, _ = self._run(["cmd"], ["/c", "chcp"])
                mm = re.search(r"(\d{3,4})", out or "")
                if mm:
                    con["code_page"] = int(mm.group(1))
                    if con["code_page"] != 65001:
                        con["preferred_encoding"] = f"cp{con['code_page']}"
            except Exception:
                pass
        else:
            try:
                import locale as _locale

                con["preferred_encoding"] = _locale.getpreferredencoding(False) or "utf-8"
            except Exception:
                pass
        con["color"] = bool(con["tty"]) and self.env.get("NO_COLOR") is None
        return con

    def _binaries(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for name in self.include:
            key = name.lower()
            if key in found:
                continue
            try:
                path = shutil.which(name, path=self.env.get("PATH"))
            except Exception:
                path = None
            if path:
                found[key] = os.path.normpath(path)
        return found

    def _binary_versions(self, binaries: dict[str, str]) -> dict[str, str]:
        """`--version` scrape for tools whose behaviour depends on major version.

        Only these are probed: each probe is a process spawn, so the list is
        deliberately short and cached with the profile.
        """
        out: dict[str, str] = {}
        for name in VERSIONED_BINARIES:
            path = binaries.get(name)
            if not path:
                continue
            rc, so, se = self._run([path], ["--version"])
            if rc not in (0, None):
                continue
            line = ((so or se).strip().splitlines() or [""])[0]
            if line:
                out[name] = line
        return out

    def _shells(self, prof: CapabilityProfile) -> dict[str, ShellProbe]:
        shells: dict[str, ShellProbe] = {}

        def register(sp: ShellProbe) -> None:
            shells[sp.dialect] = sp

        unix = [("bash", ["--noediting", "--noprofile", "--norc", "--version"]),
                ("zsh", ["--version"]), ("fish", ["--version"]), ("sh", ["--version"])]
        for name, args in unix:
            path = prof.binaries.get(name)
            if not path:
                continue
            rc, out, err = self._run([path], args)
            text = (out or err).strip().splitlines()
            ver = _parse_ver(text[0] if text else "")
            sp = ShellProbe(dialect=name, kind="unix", path=path, version=ver,
                            version_text=(text[0] if text else ""), supports_stdin_command=True)
            sp.supports_pipefail = name in ("bash", "zsh", "fish") or (name == "sh" and "dash" not in path.lower())
            sp.utf8_default = "utf" in str(prof.console.get("preferred_encoding", "")).lower()
            if name == "dash":
                sp.supports_pipefail = False
                sp.notes.append("dash: no pipefail, limited builtin set")
            if rc is None:
                sp.usable = False
                sp.notes.append(f"version probe failed: {err.strip()[:120]}")
            elif err == UNPROBED:
                sp.notes.append("version unprobed")
            if name == "bash" and ver and ver[0] == 3:
                sp.notes.append("bash 3.x (macOS default): no mapfile/associative arrays/$'...'\u00b7unicode escapes")
            register(sp)

        if not shells.get("sh") and not is_windows():
            # sh almost always exists even when not on PATH as a "binary"
            register(ShellProbe(dialect="sh", kind="unix", path="/bin/sh", supports_stdin_command=True))

        # PowerShell family
        for dialect, exe, args in (("pwsh", "pwsh", ["-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"]),
                                    ("powershell", "powershell", ["-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"])):
            path = prof.binaries.get(exe) or (("/usr/bin/pwsh",) if False else None)
            if not path and dialect == "pwsh" and not is_windows():
                path = None
            if not path:
                continue
            rc, out, err = self._run([path], args)
            text = (out or "").strip()
            ver = _parse_ver(text or err)
            sp = ShellProbe(dialect=dialect, kind="powershell", path=path, version=ver,
                            version_text=text or (err.strip().splitlines() or [""])[0])
            if rc is None:
                sp.usable, sp.notes = False, [f"probe failed: {err.strip()[:140]}"]
            elif err == UNPROBED:
                sp.notes = ["version unprobed"]
            if ver and ver[0] >= 7:
                sp.supports_native_error_action = ver >= (7, 3)
                sp.supports_stdin_command = True
                sp.utf8_default = True
                if ver < (7, 3):
                    sp.notes.append("PS<7.3: native-command stderr does not set $?; error action bridging is emulated")
            else:
                sp.notes.append("Windows PowerShell 5.1: no $PSNativeCommandUseErrorActionPreference, CLIXML on redirected stderr, UTF-16 defaults")
                sp.supports_native_error_action = False
                sp.supports_stdin_command = False
            register(sp)

        py = sys.executable or prof.binaries.get("python3") or prof.binaries.get("python")
        if py:
            rc, out, _ = self._run([py], ["-c", "import sys;print(sys.version.split()[0])"])
            sp = ShellProbe(dialect="python", kind="python", path=py,
                            version=_parse_ver(out if rc == 0 else sys.version),
                            version_text=(out.strip() if rc == 0 else platform.python_version()),
                            supports_stdin_command=True, utf8_default=True)
            if not is_windows() and sp.version >= (3, 7):
                sp.supports_pipefail = True
            register(sp)

        if is_windows() and "cmd" in prof.binaries:
            register(ShellProbe(dialect="cmd", kind="windows", path=prof.binaries["cmd"],
                                version=(10,), version_text="cmd.exe (legacy)",
                                notes=["cmd: no pipelines of exit codes, quoting is pathological - avoid"]))
        return shells

    def _modules(self) -> dict[str, bool]:
        mods = ("mcp", "watchfiles", "jsonschema", "yaml", "tomllib", "psutil", "rich", "httpx")
        out: dict[str, bool] = {}
        for m in mods:
            if m == "tomllib":
                out[m] = sys.version_info >= (3, 11)
                continue
            try:
                mod = __import__(m)
                out[m] = True
                if m == "mcp":
                    out["mcp.v2"] = int(getattr(mod, "__version__", "0").split(".")[0]) >= 2
            except Exception:
                out[m] = False
        return out

    def _capabilities(self, prof: CapabilityProfile) -> set[str]:
        caps: set[str] = set()
        b = prof.binaries
        if "rg" in b:
            caps.add("search.ripgrep")
        if {"fd", "fdfind"} & set(b):
            caps.add("search.fd")
        if "grep" in b:
            caps.add("search.grep")
        if "diff" in b:
            caps.add("fs.diff")
        if "patch" in b:
            caps.add("fs.patch.gnu")
        if "git" in b:
            caps.add("vcs.git")
        if {"tar"} & set(b):
            caps.add("archive.tar")
        if "7z" in b:
            caps.add("archive.7z")
        if prof.python_modules.get("mcp"):
            caps.add("transport.mcp")
        if prof.python_modules.get("mcp.v2"):
            caps.add("transport.mcp.v2")
        if prof.python_modules.get("watchfiles"):
            caps.add("fs.watch")
        if any(s.kind == "powershell" and s.usable for s in prof.shells.values()):
            caps.add("shell.powershell")
        if any(s.kind == "unix" and s.usable for s in prof.shells.values()):
            caps.add("shell.unix")
        if prof.filesystem.get("symlinks"):
            caps.add("fs.symlinks")
        if prof.filesystem.get("hardlinks"):
            caps.add("fs.hardlinks")
        if prof.python_modules.get("psutil"):
            caps.add("proc.psutil")
        if prof.python_modules.get("yaml"):
            caps.add("config.yaml")
        if prof.env.get("CI"):
            caps.add("env.ci")
        if prof.env.get("WSL_DISTRO_NAME"):
            caps.add("env.wsl")
        return caps

    def _warn(self, prof: CapabilityProfile) -> list[str]:
        w: list[str] = []
        if prof.os == "windows" and "pwsh" not in prof.shells:
            w.append("no pwsh on PATH: falling back to Windows PowerShell 5.1 semantics (encoding/CLIXML caveats)")
        bash = prof.shells.get("bash")
        if bash and bash.version and bash.version[0] < 4:
            w.append(f"bash {bash.major}.x detected: modern syntax unavailable (assoc arrays, ** globbing)")
        if prof.filesystem.get("case_sensitive") is False and "search.ripgrep" in prof.capabilities:
            w.append("case-insensitive filesystem: match paths case-insensitively to avoid false misses")
        if not prof.binaries.get("git"):
            w.append("git missing: journal rollback and vsc-dependent tools stay available but diff helpers degrade")
        if prof.filesystem.get("probe_error"):
            w.append(f"filesystem probe incomplete: {prof.filesystem['probe_error']}")
        return w


def _os_name() -> str:
    s = platform.system().lower()
    return {"windows": "windows", "darwin": "darwin", "linux": "linux"}.get(s, s or "unknown")


def _os_release() -> str:
    if is_windows():
        try:
            return "Windows " + " ".join(platform.win32_ver()[:2])
        except Exception:
            return "Windows"
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


def _fs_type(path: str) -> str | None:
    if is_windows():
        return None
    try:
        proc = subprocess.run(["df", "-T", path], capture_output=True, text=True, timeout=5,
                              stdin=subprocess.DEVNULL)
        parts = proc.stdout.split()
        if len(parts) >= 2:
            return parts[1]
    except Exception:
        return None
    return None


def _win_flags() -> int:
    if not is_windows():
        return 0
    return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


# ----------------------------------------------------------------- fingerprint


def compute_fingerprint(prof: CapabilityProfile, *, extra: dict[str, Any] | None = None) -> str:
    payload = {
        "os": prof.os, "arch": prof.arch, "env": env_fingerprint(prof.env or None),
        "shells": {k: [v.path, list(map(int, v.version)), v.usable] for k, v in sorted(prof.shells.items())},
        "binaries": prof.binaries, "caps": sorted(prof.capabilities),
        "fs": {k: prof.filesystem.get(k) for k in ("case_sensitive", "symlinks", "hardlinks", "fs_type")},
        "schema": CURRENT_SCHEMA, "extra": extra or {},
    }
    # note: binaries/shells may be empty on a partial probe; still deterministic
    from .util import compact_json

    payload["shells"] = {k: [v["path"], v["version"], v["usable"]] if isinstance(v, dict)
                         else [v.path, list(map(str, v.version)), v.usable]
                         for k, v in sorted(prof.shells.items())}
    return short_hash(compact_json(payload), 16)


def load_cached_profile(path: str, fingerprint: str, *, ttl: float) -> CapabilityProfile | None:
    try:
        import json

        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    if raw.get("fingerprint") != fingerprint or int(raw.get("schema") or 0) != CURRENT_SCHEMA:
        return None
    if ttl and (time.time() - float(raw.get("probed_at") or 0)) > ttl:
        return None
    try:
        return CapabilityProfile.from_dict(raw)
    except Exception:
        return None


def save_cached_profile(path: str, prof: CapabilityProfile) -> None:
    import json

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(prof.to_dict(include_receipts=False), fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
