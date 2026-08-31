"""Toolkit - the assembly layer. One place that decides what is possible here.

    build() -> Config -> CapabilityProfile -> PathSandbox -> FsJournal -> Fs
                   -> ShellRunner -> Registry(+builtins,+skills,+drop-ins) -> Engine

Everything downstream (first-party autopilot, MCP server, CLI, tests) builds a
Toolkit instead of re-implementing that wiring. `describe()` returns the whole
decision as data, which is what makes "why did the agent get these tools?"
answerable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .core.config import Config
from .core.engine import Engine
from .core.ledger import Ledger
from .core.profile import CapabilityProfile, Prober
from .core.registry import Registry
from .fsx.journal import FsJournal
from .fsx.ops import Fs
from .fsx.sandbox import PathSandbox, SandboxPolicy
from .fsx.search import SearchBackend
from .shells.base import ShellRunner
from .skills.loader import SkillLoader
from .tools import builtin


@dataclass
class Toolkit:
    config: Config
    profile: CapabilityProfile
    sandbox: PathSandbox
    fs: Fs
    journal: FsJournal
    shells: ShellRunner
    registry: Registry
    engine: Engine
    skills: SkillLoader
    ledger: Ledger | None = None
    build_report: dict[str, Any] = field(default_factory=dict)

    @property
    def advertised(self) -> list[str]:
        return self.engine.advertise().names

    @property
    def workspace(self):
        """The workspace root as a path - what tests and hosts compare results against."""
        import pathlib

        return pathlib.Path(self.config.workspace)

    def describe(self) -> dict[str, Any]:
        snap = self.engine.advertise()
        return {
            "workspace": self.config.workspace,
            "roots": [os.path.basename(r) or r for r in self.sandbox.roots],
            "profile": {"os": self.profile.os, "arch": self.profile.arch,
                        "shells": self.profile.available_dialects(),
                        "preferred_dialect": self.profile.preferred_dialect(),
                        "capabilities": sorted(self.profile.capabilities),
                        "fingerprint": self.profile.fingerprint,
                        "warnings": self.profile.warnings},
            "policy": {"read_only": self.config.policy.read_only,
                       "auto_approve": self.config.policy.auto_approve,
                       "require_approval": self.config.policy.require_approval,
                       "deny_rules": len(self.config.policy.deny)},
            "tools": {"registered": len(self.registry.all()), "advertised": len(snap.tools),
                      "estimated_tokens": snap.tokens, "digest": snap.digest,
                      "providers": snap.selected},
            "skills": {"discovered": len(self.skills.discover()),
                       "errors": self.skills.errors,
                       "names": [s.name for s in self.skills.discover()]},
            "state": {"dir": self.config.state.dir, "journal": self.journal.summary(),
                      "ledger": self.ledger.path if self.ledger else None},
            "build": self.build_report,
        }

    def close(self) -> None:
        try:
            self.engine.close()
        finally:
            if self.ledger:
                self.ledger.close()

    def __enter__(self) -> Toolkit:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def build(*, config: Config | None = None, overrides: dict[str, Any] | None = None,
          cwd: str | None = None, approver: Any = None, force_probe: bool = False,
          roots: list[str] | None = None, read_only: bool | None = None,
          load_dropins: bool = True) -> Toolkit:
    report: dict[str, Any] = {}
    cfg = config or Config.load(cwd=cwd, overrides=_mk_overrides(overrides, roots, read_only))
    if roots:
        cfg.roots = [os.path.abspath(os.path.expanduser(r)) for r in roots]
        cfg.workspace = cfg.roots[0]

    profile = Prober(env=dict(os.environ)).probe(
        roots=cfg.roots,
        cache_path=os.path.join(cfg.state.dir, "profile.json") if cfg.state.profile_cache else None,
        ttl=cfg.state.profile_ttl_s, force=force_probe)
    report["profile_source"] = "probed"

    policy = SandboxPolicy(follow_symlinks=cfg.fs.follow_symlinks, deny=cfg.fs.deny,
                           ignore=cfg.fs.ignore, allow_dotfiles=cfg.fs.allow_dotfiles,
                           reject_device_names=cfg.fs.reject_device_names,
                           long_path_prefix=cfg.fs.long_path_prefix)
    sandbox = PathSandbox(cfg.roots, policy, cwd=cfg.workspace)
    journal = FsJournal(os.path.join(cfg.state.dir, "journal"), enabled=cfg.state.journal,
                        keep=cfg.state.keep_snapshots, sandbox=sandbox)
    fs = Fs(sandbox, atomic=cfg.fs.atomic_write, newline_policy=cfg.fs.newline, encoding=cfg.fs.encoding,
            max_read_bytes=cfg.budget.max_read_bytes, max_write_bytes=cfg.budget.max_write_bytes,
            journal=journal, delete_mode=cfg.fs.trash)
    # deny wins: an earlier version appended deny_dialects here, which made the
    # setting a way to *enable* a dialect.
    allowed = [d for d in cfg.shell.allow_dialects if d not in set(cfg.shell.deny_dialects)]
    shells = ShellRunner(profile, allowed_dialects=allowed,
                         tempdir=cfg.shell.tempdir or os.path.join(cfg.state.dir, "shell"),
                         kill_tree=cfg.shell.kill_tree, utf8_enforce=cfg.shell.utf8_enforce,
                         allow_legacy_powershell=cfg.shell.allow_legacy_powershell,
                         strip_ansi_output=cfg.shell.strip_ansi,
                         max_output_bytes=cfg.shell.max_output_bytes,
                         sessions_enabled=cfg.shell.sessions_enabled)
    os.makedirs(cfg.shell.tempdir or os.path.join(cfg.state.dir, "shell"), exist_ok=True)

    ledger = Ledger(os.path.join(cfg.state.dir, "ledger.ndjson"), enabled=cfg.state.ledger,
                    redact=cfg.state.redact)
    registry = Registry(profile=profile)
    engine = Engine(config=cfg, registry=registry, profile=profile, approver=approver, ledger=ledger,
                    fs=fs, journal=journal)
    engine.attach(search_backends=SearchBackend(sandbox, profile))

    # 1. built-ins
    rep = builtin.register(registry, engine=engine, shells=shells, fs=fs, journal=journal)
    report["builtin"] = rep
    # 2. skills -> tool manifests (declarative), and the skills.* tools themselves
    skills = SkillLoader(cfg.skills.dirs, max_body_bytes=cfg.skills.max_body_bytes,
                             profile=profile, respect_priority=cfg.skills.respect_priority,
                             max_inline_tokens=cfg.skills.max_inline_tokens)
    engine.attach(skills=skills)
    _register_skill_tools(registry, engine=engine, skills=skills)
    # 3. drop-in python tools
    if load_dropins:
        added = []
        for d in cfg.tools.dropin_dirs:
            r = registry.load_dir(d, replace=cfg.tools.override_builtin)
            if r["files"] or r["errors"]:
                added.append(r)
        report["dropin"] = added
        if cfg.tools.entry_points:
            report["entry_points"] = registry.load_entry_points()
    report["registered_after_load"] = len(registry.all())
    return Toolkit(config=cfg, profile=profile, sandbox=sandbox, fs=fs, journal=journal, shells=shells,
                   registry=registry, engine=engine, skills=skills, ledger=ledger, build_report=report)


def _mk_overrides(overrides: dict[str, Any] | None, roots: list[str] | None,
                  read_only: bool | None) -> dict[str, Any]:
    out = dict(overrides or {})
    if roots:
        out["roots"] = list(roots)
    if read_only is not None:
        out["policy"] = {**(out.get("policy") or {}), "read_only": read_only}
    return out


def _register_skill_tools(reg: Registry, *, engine: Engine, skills: SkillLoader) -> None:
    from .core.manifest import ToolManifest

    def skills_list(refresh: bool = False) -> dict[str, Any]:
        found = skills.discover(refresh=refresh)
        return {"skills": [s.to_dict() for s in found], "count": len(found),
                "dirs": skills.dirs, "errors": skills.errors,
                "total_tokens": sum(s.token_estimate for s in found)}

    def skills_load(name: str, references: list[str] | None = None,
                    max_tokens: int | None = None) -> dict[str, Any]:
        skill = skills.get(name)
        if max_tokens is None:
            max_tokens = skills.max_inline_tokens
        return {"skill": skill.name, "version": skill.version, "path": skill.path,
                "injection": skill.render_injection(max_tokens=int(max_tokens),
                                                     with_references=references or []),
                "tokens": skill.token_estimate, "references": skill.references,
                "scripts": skill.scripts,
                "declared_tools": [t.get("id") for t in skill.tools if t.get("id")],
                "allowed_tools": skill.allowed_tools}

    def skills_match(task: str, limit: int = 3, max_tokens: int | None = None) -> dict[str, Any]:
        block = skills.context_block(task, limit=int(limit),
                                     max_tokens=None if max_tokens is None else int(max_tokens))
        return {"task": task[:300], **block,
                "note": "inject `block` verbatim into the prompt; it lists which skills matched and why"}

    reg.register(ToolManifest(
        id="skills.list", title="List skills",
        description="Discovered skills with triggers, token cost, and load errors. Skills are procedural "
                    "knowledge; call skills.load to get one.",
        capability="skills.list", risk="none", typical_latency_ms=6, tags=["skills", "instructions", "howto"],
        input_schema={"type": "object", "properties": {"refresh": {"type": "boolean", "default": False}},
                      "additionalProperties": False}), skills_list)
    reg.register(ToolManifest(
        id="skills.load", title="Load a skill",
        description="Return a skill's instruction body (budgeted) plus optional reference files, ready to "
                    "inject into context.",
        capability="skills.load", risk="none", typical_latency_ms=8, tags=["skills", "read", "instructions"],
        input_schema={"type": "object", "properties": {"name": {"type": "string"},
                                                       "references": {"type": "array", "items": {"type": "string"}},
                                                       "max_tokens": {"type": "integer", "minimum": 100,
                                                                     "maximum": 8000,
                                                       "default": skills.max_inline_tokens,
                                                       "description": "defaults to "
                                                                      "skills.max_inline_tokens"}},
                    "required": ["name"], "additionalProperties": False}), skills_load)
    reg.register(ToolManifest(
        id="skills.match", title="Match skills to a task",
        description="Given task text, return which skills apply (with reasoning) and a prepared context "
                    "block. Explainable lexical matching, no model call.",
        capability="skills.match", risk="none", typical_latency_ms=6,
        tags=["skills", "match", "trigger", "context", "inject"],
        input_schema={"type": "object", "properties": {"task": {"type": "string", "minLength": 1},
                                                        "limit": {"type": "integer", "minimum": 1, "maximum": 10,
                                                                  "default": 3},
                                                        "max_tokens": {"type": "integer", "minimum": 200,
                                                                      "maximum": 8000,
                                                        "default": skills.max_inline_tokens * 3,
                                                        "description": "block budget; defaults to "
                                                                       "skills.max_inline_tokens x limit"}},
                    "required": ["task"], "additionalProperties": False}), skills_match)

    # declarative tools declared by skills (tool.toml) -> real manifests with no handler
    for cand in skills.manifest_candidates():
        try:
            man = ToolManifest.from_dict({**cand, "advertised": cand.get("advertised", True),
                                          "hidden_reason": cand.get("hidden_reason")
                                          or "skill-declared tool without a handler (Phase 2 executor)"},
                                         source=cand.get("source", "skill"))
            man.meta["declares"] = True
            if not man.description:
                man.description = "Declared by a skill; execution wiring lands with the skill runtime."
            reg.register(man, _unavailable_skill_handler(man.id))
        except Exception as exc:
            engine.registry.load_errors.append({"skill_tool": cand.get("id", "?"), "stage": "declare",
                                                "error": str(exc)[:300]})


def _unavailable_skill_handler(tool_id: str):
    from .core.errors import E as _E
    from .core.errors import SkeletonKeyError as _S

    def _h(**_kw: Any) -> Any:
        raise _S(_E.NOT_IMPLEMENTED, f"skill-declared tool {tool_id!r} has no executor yet",
                 details={"phase": "2 (skill runtime + tool compiler)", "plan": "PLAN.md"},
                 next_actions=[{"tool": "registry.search", "args": {"query": tool_id}}])
    return _h
