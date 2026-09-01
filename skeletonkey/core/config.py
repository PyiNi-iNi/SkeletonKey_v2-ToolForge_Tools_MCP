"""Layered configuration.

Precedence (later wins):
  built-in defaults  <  $SKELETONKEY_CONFIG  <  ./skeletonkey.toml  <
  <config-dir>/config.toml  <  SKELETONKEY_* env vars  <  explicit overrides

`roots` is the security-critical list: the only filesystem locations tools may
touch. Empty roots + `auto_workspace=True` means "whatever the MCP client
declares via roots/list, else the CWD" - resolved once at engine start and
frozen for the run, so a mid-run chdir cannot widen the sandbox.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any

from .coerce import coerce_value, to_bool, to_float, to_int

__all__ = ["ENV_PREFIX", "Config", "to_bool", "to_float", "to_int"]

ENV_PREFIX = "SKELETONKEY_"
CONFIG_FILENAMES = ("skeletonkey.toml", ".skeletonkey.toml")


@dataclass
class PolicyConfig:
    """Risk gating. `auto_approve` is the autopilot dial; deny still wins.

    Rules as data (P3): `rule` is the structured form - deny/allow/escalate/
    rate_limit with path-glob, argv-prefix and env-name matchers, compiled by
    `core/policy.py`. The legacy spellings (`deny` strings, `escalate` list,
    `rate_limits` map) compile to the same rule objects, so one matcher serves
    all three.
    """

    read_only: bool = False
    # risk classes the agent may perform without a human turn
    auto_approve: list[str] = field(default_factory=lambda: ["none", "read", "write"])
    require_approval: list[str] = field(default_factory=lambda: ["destructive", "privileged", "network"])
    # never allowed, regardless of flags. Grammar: `tool-id-or-glob(arg-glob)`; the
    # pattern is tested against every string argument (and, for path-ish keys, against
    # the basename too). Argv/script *content* matching is deliberately absent here -
    # a script that merely mentions a word is not the same fact as a call that passes
    # it as an argument; use a structured `rule` with `argv_prefix` for that.
    deny: list[str] = field(default_factory=lambda: [
        "fs.delete(**/.ssh/**)", "fs.delete(**/cookies*)",
    ])
    # structured rules: [[policy.rule]] tables (action = deny|allow|escalate|rate_limit,
    # tool, paths, argv_prefix, env, reason, rate, window_s). See core/policy.py for
    # the full grammar and what each action may and may not override.
    rule: list[dict] = field(default_factory=list)
    # tools whose risk we treat as higher than declared (paranoia dial)
    escalate: list[str] = field(default_factory=list)
    max_timeout_s: float = 900.0
    confirm_destructive: bool = True
    deny_outside_roots: bool = True
    follow_symlinks: str = "within-roots"        # never | within-roots | always
    # per-tool rate limits: tool id (or glob) -> max calls per rate_window_s.
    # The default keeps a bulk-delete loop from erasing a workspace before the
    # budget governor's per-task cap has a chance to say anything.
    rate_limits: dict[str, int] = field(default_factory=lambda: {"fs.delete": 20})
    rate_window_s: float = 60.0
    # mutations circuit breaker: at most this many *successful* mutating calls
    # per rolling 60s on one engine, whatever the task caps say. 0 disables it.
    max_mutations_per_minute: int = 120


@dataclass
class BudgetConfig:
    """Governor limits for an unattended run. 0/None = unlimited."""

    max_output_bytes: int = 24_000
    max_result_tokens: int = 6_000
    task_max_wall_s: float = 1800.0
    task_max_calls: int = 400
    task_max_mutations: int = 150
    task_max_tokens_out: int = 400_000
    per_tool_max_bytes: dict[str, int] = field(default_factory=dict)
    spill_dir: str | None = None            # None -> <state_dir>/spill
    max_read_bytes: int = 4_000_000
    max_write_bytes: int = 20_000_000


@dataclass
class ShellConfig:
    default_dialect: str | None = None      # None = profile.preferred_dialect()
    allow_dialects: list[str] = field(default_factory=lambda: ["bash", "pwsh", "python", "powershell", "sh", "zsh"])
    deny_dialects: list[str] = field(default_factory=list)
    strict_bash: bool = True                # set -euo pipefail
    strict_pwsh: bool = True                # $ErrorActionPreference / native error action
    utf8_enforce: bool = True
    strip_ansi: bool = False
    timeout_s: float = 120.0
    kill_tree: bool = True
    sessions_enabled: bool = True
    persist_env: bool = True                # capture env deltas across calls
    tempdir: str | None = None
    # Windows: prefer powershell.exe when pwsh absent (semantics differ!)
    allow_legacy_powershell: bool = True
    max_output_bytes: int = 200_000


@dataclass
class FsConfig:
    follow_symlinks: str = "within-roots"
    atomic_write: bool = True
    newline: str = "preserve"               # preserve | lf | crlf | native
    encoding: str = "utf-8"
    reject_device_names: bool = True
    long_path_prefix: bool = True           # \\?\ on Windows
    trash: str = "journal"                  # journal | os-trash (recycle bin + journal) | delete
    backup_suffix: str | None = None
    ignore: list[str] = field(default_factory=lambda: [
        ".git/**", "node_modules/**", "__pycache__/**", ".venv/**", "venv/**",
        ".mypy_cache/**", ".pytest_cache/**", "dist/**", "build/**", ".next/**",
        "target/**", "*.pyc", ".DS_Store",
    ])
    deny: list[str] = field(default_factory=lambda: [
        "**/.env", "**/.env.*", "**/id_rsa", "**/id_ed25519", "**/.ssh/**",
        "**/*.pem", "**/*.key", "**/credentials", "**/.gitconfig",
        "**/.aws/credentials", "**/.netrc", "**/secrets.*",
    ])
    allow_dotfiles: bool = True


@dataclass
class SkillConfig:
    dirs: list[str] = field(default_factory=lambda: ["skills"])
    auto_load: bool = True
    max_body_bytes: int = 32_000
    max_inline_tokens: int = 1_200
    respect_priority: bool = True
    # Installing is how a skill's script starts running, so the door is shut by default and
    # `skills.install` refuses until an operator opens it (PLAN P3 gives it a policy to obey).
    allow_install: bool = False
    install_root: str = ""                       # empty = the first entry of `dirs`


@dataclass
class ToolConfig:
    dropin_dirs: list[str] = field(default_factory=lambda: ["tools"])
    entry_points: bool = True
    hot_reload: bool = False
    override_builtin: bool = False
    enable: list[str] = field(default_factory=list)    # empty = all
    disable: list[str] = field(default_factory=list)
    gate_by_profile: bool = True
    # P5b: the semantic routing stage. Off by default = the deterministic lexical
    # path, which is the tested default and what existing hosts keep seeing. On =
    # `registry.route(semantic=True)` blends lexical 50/50 with a discovered
    # `skeletonkey.semantic` backend (the builtin zero-dep lexical-tfidf ships,
    # ADR-0012; entry points plug in the same way).
    semantic: bool = False


@dataclass
class AdvertiseConfig:
    """Per-tier advertisement budgets (P5a). 0 = no cap for that dimension.

    `full` mirrors the legacy `mcp.advertise_max_tools` knob: the MCP bridge still
    honours the legacy knob for the full tier, and `[advertise]` is what the tiered
    path uses. core = what a host sees on a fresh session, before any expand.
    """

    core_max_tools: int = 20
    core_max_tokens: int = 1200
    task_max_tools: int = 48
    task_max_tokens: int = 6000
    full_max_tools: int = 0
    full_max_tokens: int = 0

    def budgets(self) -> dict[str, dict[str, int]]:
        return {
            "core": {"tools": self.core_max_tools, "tokens": self.core_max_tokens},
            "task": {"tools": self.task_max_tools, "tokens": self.task_max_tokens},
            "full": {"tools": self.full_max_tools, "tokens": self.full_max_tokens},
        }


@dataclass
class McpConfig:
    name: str = "skeletonkey"
    instructions: str = "Adaptive filesystem + multi-shell toolkit. Prefer capability lookups over guessing names."
    transport: str = "stdio"                            # stdio | streamable-http
    host: str = "127.0.0.1"
    port: int = 8765
    advertise_max_tools: int = 48
    advertise_dialect_guides: bool = True
    emit_list_changed: bool = True
    structured_content: bool = True
    log_to_stderr: bool = False                          # stdout is the protocol channel
    # P5b (ADR-0013): remote MCP servers, enrolled at build time as
    # `remote.<name>.<tool>`. Each entry: {"command": [...], "args": [...]} for
    # stdio, or {"url": "..."} for streamable-http, plus optional "enabled"
    # (default true) and "timeout_s" (default 30).
    remotes: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class StateConfig:
    dir: str = ""                                        # default <repo>/.sk or ~/.skeletonkey
    journal: bool = True
    ledger: bool = True
    profile_cache: bool = True
    profile_ttl_s: float = 3600.0
    keep_snapshots: int = 200
    redact: bool = True


@dataclass
class PublishConfig:
    # Empty = <user config dir>/skeletonkey/publish/store.json. The store is
    # deliberately OUTSIDE the workspace roots: the fs sandbox is the wall that
    # keeps fs.* tools away from it (see ADR-0010). Override for tests/CI.
    store_path: str = ""


@dataclass
class LiveConfig:
    """The `live.*` group (docs/LIVE-HMR.md): Python HMR over a LiveREPL.

    Executing code is the whole point of start/repl/patch, so those tools are
    risk=write like shell.run and obey the same policy surface (deny rules,
    read_only, approvals, tools.disable). The knobs here are resource and
    panel shaping, not trust decisions."""
    enabled: bool = True                  # false = the live.* tools refuse (like a gate)
    watch_interval_s: float = 0.35        # polling baseline; watchfiles backend ignores this
    debounce_ms: int = 120                # editor save-storm collapsing window
    max_programs: int = 8
    max_source_bytes: int = 400_000
    exec_guard_s: float = 10.0            # wall-clock leash on repl/patch/reload bodies
    repl_max_output_bytes: int = 16_000
    state_value_max_repr: int = 400
    snapshots_max: int = 16
    auto_render: bool = True              # render() after every mutation by default
    # the preview panel (live.serve). POST /repl executes code: loopback by
    # default, and panel_repl=false turns the page into a read-only viewer.
    host: str = "127.0.0.1"
    port: int = 8010
    panel_repl: bool = True


@dataclass
class Config:
    roots: list[str] = field(default_factory=list)
    cwd: str = ""
    workspace: str = ""
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    fs: FsConfig = field(default_factory=FsConfig)
    skills: SkillConfig = field(default_factory=SkillConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    advertise: AdvertiseConfig = field(default_factory=AdvertiseConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    state: StateConfig = field(default_factory=StateConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    log_level: str = "WARNING"
    source_files: list[str] = field(default_factory=list)
    overrides_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    @classmethod
    def load(cls, *, path: str | None = None, cwd: str | None = None,
             overrides: dict[str, Any] | None = None, env: dict[str, str] | None = None) -> Config:
        env = env if env is not None else dict(os.environ)
        cfg = cls()
        cfg.cwd = os.path.abspath(cwd or env.get("PWD") or os.getcwd())

        # Lowest precedence first; every later source overwrites earlier ones.
        # (A user-level file must never silently win over the project's own.)
        candidates: list[str] = [os.path.join(_user_dir(), "config.toml")]
        cfg_dir = env.get(ENV_PREFIX + "CONFIG_DIR") or os.path.join(cfg.cwd, "config")
        candidates.append(os.path.join(cfg_dir, "config.toml"))
        candidates.extend(os.path.join(cfg.cwd, name) for name in reversed(CONFIG_FILENAMES))
        if env.get(ENV_PREFIX + "CONFIG"):
            candidates.append(env[ENV_PREFIX + "CONFIG"])
        if path:
            # os.fspath: a Path must be legal here, and source_files has to stay
            # JSON-serializable for the config resource and `sk doctor`.
            candidates.append(os.fspath(path))

        for cand in candidates:
            if cand and os.path.isfile(cand):
                data = _read_toml(cand)
                if data is None:
                    cfg.warnings.append(f"config file {cand} is not valid TOML and was ignored")
                elif data:
                    _apply_mapping(cfg, data, src=cand)
                    cfg.source_files.append(cand)
                else:
                    cfg.source_files.append(cand)

        _apply_env(cfg, env)
        if overrides:
            _apply_mapping(cfg, overrides, src="overrides")
            cfg.overrides_applied.extend(f"override:{k}" for k in overrides)

        cfg.workspace = cfg.roots[0] if cfg.roots else cfg.cwd
        if not cfg.roots:
            cfg.roots = [cfg.cwd]
        cfg.roots = [os.path.abspath(os.path.expanduser(p)) for p in cfg.roots]
        if not cfg.state.dir:
            cfg.state.dir = os.path.join(cfg.workspace, ".sk")
        if not cfg.budget.spill_dir:
            cfg.budget.spill_dir = os.path.join(cfg.state.dir, "spill")
        cfg.skills.dirs = _abs_all(cfg.skills.dirs, cfg.workspace)
        cfg.tools.dropin_dirs = _abs_all(cfg.tools.dropin_dirs, cfg.workspace)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return _as_dict(self)

    def redacted_dict(self) -> dict[str, Any]:
        from .redact import redact_obj

        return redact_obj(self.to_dict())


# ---------------------------------------------------------------------- plumbing


def _user_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config" if os.name != "nt" else "")
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "skeletonkey")


def _read_toml(path: str) -> dict[str, Any] | None:
    """None means "could not parse" - callers must say so rather than shrug."""
    try:
        import tomllib
    except ImportError:
        return None
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh) or {}
    except (OSError, ValueError):
        return None


def _abs_all(paths: list[str], base: str) -> list[str]:
    out = []
    for p in paths:
        q = os.path.expanduser(p)
        out.append(q if os.path.isabs(q) else os.path.join(base, q))
    return out


def _as_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return {f.name: _as_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_as_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    return obj


def _coerce(value: Any, target: Any) -> Any:
    if isinstance(target, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(target, int) and not isinstance(target, bool):
        return int(value)
    if isinstance(target, float):
        return float(value)
    if isinstance(target, list):
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return list(value)
    return value


def _apply_mapping(cfg: Config, data: dict[str, Any], *, src: str) -> None:
    for key, value in data.items():
        if not hasattr(cfg, key):
            cfg.overrides_applied.append(f"ignored-unknown:{src}:{key}")
            continue
        current = getattr(cfg, key)
        if is_dataclass(current) and isinstance(value, dict):
            for sub_key, sub_val in value.items():
                _set_path(cfg, f"{key}.{sub_key}", sub_val, src=src, notes=cfg.overrides_applied)
        else:
            try:
                if isinstance(current, list) and isinstance(value, str):
                    value = [v.strip() for v in value.split(",") if v.strip()]
                setattr(cfg, key, coerce_value(_type_hints(Config).get(key), value, current))
            except (TypeError, ValueError):
                cfg.overrides_applied.append(f"bad-value:{src}:{key}")


_ENV_MAP = {
    "ROOTS": ("roots", list),
    "READ_ONLY": ("policy.read_only", bool),
    "AUTO_APPROVE": ("policy.auto_approve", list),
    "REQUIRE_APPROVAL": ("policy.require_approval", list),
    "DENY": ("policy.deny", list),
    "MAX_OUTPUT_BYTES": ("budget.max_output_bytes", int),
    "MAX_RESULT_TOKENS": ("budget.max_result_tokens", int),
    "TASK_MAX_CALLS": ("budget.task_max_calls", int),
    "SHELL_TIMEOUT": ("shell.timeout_s", float),
    "SHELL_STRICT": ("shell.strict_bash", bool),
    "ALLOWED_DIALECTS": ("shell.allow_dialects", list),
    "DEFAULT_DIALECT": ("shell.default_dialect", str),
    "FS_DENY": ("fs.deny", list),
    "SKILL_DIRS": ("skills.dirs", list),
    "TOOL_DIRS": ("tools.dropin_dirs", list),
    "DISABLE_TOOLS": ("tools.disable", list),
    "LOG_LEVEL": ("log_level", str),
    "STATE_DIR": ("state.dir", str),
    "MCP_TRANSPORT": ("mcp.transport", str),
    "MCP_PORT": ("mcp.port", int),
    "ADVERTISE_MAX": ("mcp.advertise_max_tools", int),
    "NO_JOURNAL": ("state.journal", lambda v: not _truthy(v)),
}

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off", ""}


def _truthy(value: str) -> bool:
    return value.strip().lower() in TRUE_WORDS


def _set_path(cfg: Config, dotted: str, value: Any, *, src: str, notes: list[str]) -> None:
    parts = dotted.split(".")
    target: Any = cfg
    for p in parts[:-1]:
        target = getattr(target, p, None)
        if target is None:
            notes.append(f"ignored-unknown:{src}:{dotted}")
            return
    leaf = parts[-1]
    if not hasattr(target, leaf):
        notes.append(f"ignored-unknown:{src}:{dotted}")
        return
    current = getattr(target, leaf)
    if isinstance(current, list) and isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    try:
        hints = _type_hints(type(target))
        setattr(target, leaf, coerce_value(hints.get(leaf), value, current))
    except (TypeError, ValueError):
        notes.append(f"bad-value:{src}:{dotted}={value!r}")


_HINT_CACHE: dict[type, dict[str, Any]] = {}


def _type_hints(cls: type) -> dict[str, Any]:
    hints = _HINT_CACHE.get(cls)
    if hints is None:
        try:
            from typing import get_type_hints

            hints = get_type_hints(cls)
        except Exception:
            hints = {}
        _HINT_CACHE[cls] = hints
    return hints


def _apply_env(cfg: Config, env: dict[str, str]) -> None:
    for name, value in env.items():
        if not name.startswith(ENV_PREFIX) or value is None:
            continue
        key = name[len(ENV_PREFIX):]
        spec = _ENV_MAP.get(key)
        if not spec:
            # generic escape hatch: SKELETONKEY_<SECTION>__<FIELD>, any depth
            if "__" in key:
                dotted = ".".join(p.lower() for p in key.split("__"))
                _set_path(cfg, dotted, value, src=f"env:{name}", notes=cfg.overrides_applied)
            continue
        path, kind = spec
        try:
            if callable(kind) and not isinstance(kind, type):
                # custom transform (e.g. NO_JOURNAL inverts the boolean)
                parts = path.split(".")
                target: Any = cfg
                for p in parts[:-1]:
                    target = getattr(target, p)
                setattr(target, parts[-1], kind(value))
                cfg.overrides_applied.append(f"env:{name}")
                continue
            if kind is bool and isinstance(value, str):
                value = to_bool(value, False)
            elif kind is int and isinstance(value, str):
                value = to_int(value, 0)
            elif kind is float and isinstance(value, str):
                value = to_float(value, 0.0)
            _set_path(cfg, path, value, src=f"env:{name}", notes=cfg.overrides_applied)
            cfg.overrides_applied.append(f"env:{name}")
        except (TypeError, ValueError):
            cfg.overrides_applied.append(f"bad-env:{name}={value!r}")
