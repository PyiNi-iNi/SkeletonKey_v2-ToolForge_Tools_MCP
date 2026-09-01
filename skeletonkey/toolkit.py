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

from .core.config import Config, _user_dir
from .core.engine import Engine
from .core.ledger import Ledger
from .core.profile import CapabilityProfile, Prober
from .core.publish import PublishStore
from .core.registry import Registry
from .fsx.journal import FsJournal
from .fsx.ops import Fs
from .fsx.sandbox import PathSandbox, SandboxPolicy
from .fsx.search import SearchBackend
from .shells.base import ShellRunner
from .shells.execute import run_script
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
    publish: PublishStore | None = None
    build_report: dict[str, Any] = field(default_factory=dict)

    @property
    def advertised(self) -> list[str]:
        return self.engine.advertise().names

    @property
    def workspace(self):
        """The workspace root as a path - what tests and hosts compare results against."""
        import pathlib

        return pathlib.Path(self.config.workspace)

    def plan(self, task: str, *, k: int = 5, skills_limit: int = 3) -> dict[str, Any]:
        """The autopilot loop's integration surface (P4): everything it needs to plan a turn.

        A ranked shortlist of tools (the deterministic lexical ranking - P5 adds the
        optional semantic stage), the skills matched to the task, the exact budgets to
        charge, and a replayable `sk call` invocation per shortlist row. The loop consumes
        this; that is what ends the ad-hoc glue.
        """
        from .core.util import compact_json

        # P5a: plan() routes - exact-name + lexical with the intent synonym layer,
        # each hit carrying its reasons - instead of bare search(), so the loop sees
        # why a tool was shortlisted, not just that it was.
        routed = self.registry.route(task, k=int(k),
                                     semantic=bool(getattr(self.config.tools, "semantic", False)))
        rows: list[dict[str, Any]] = []
        for s in routed["results"]:
            man = self.registry.get(s["id"])
            args = dict(man.examples[0].get("args", {})) if man.examples else {}
            rows.append({
                **s,
                "tokens_estimate": man.tokens_estimate(),
                "typical_output_bytes": man.typical_output_bytes,
                "replay": {"tool": man.id, "args": args,
                           "sk_call": f"sk call {man.id} '{compact_json(args)}'"},
            })
        block = self.skills.context_block(task, limit=max(1, int(skills_limit)))
        cfg = self.config
        return {
            "task": task,
            "mode": routed["mode"],
            "backend": routed.get("backend"),
            "shortlist": rows,
            "skills": block,
            "budgets": {"task_max_calls": cfg.budget.task_max_calls,
                        "task_max_mutations": cfg.budget.task_max_mutations,
                        "task_max_tokens_out": cfg.budget.task_max_tokens_out,
                        "task_max_wall_s": cfg.budget.task_max_wall_s,
                        "max_output_bytes": cfg.budget.max_output_bytes,
                        "per_tool_max_bytes": dict(cfg.budget.per_tool_max_bytes)},
        }

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
                      "providers": snap.selected, "tier": snap.tier,
                      "active_tier": self.registry.active_tier,
                      "receipts": snap.selection_receipts,
                      "budget_drops": snap.budget_drops},
            "skills": {"discovered": len(self.skills.discover()),
                       "errors": self.skills.errors,
                       "names": [s.name for s in self.skills.discover()]},
            "state": {"dir": self.config.state.dir, "journal": self.journal.summary(),
                      "ledger": self.ledger.path if self.ledger else None},
            "build": self.build_report,
        }

    def sync_skills(self, *, refresh: bool = True) -> dict[str, Any]:
        """Re-discover the skills and re-apply their tool declarations.

        This is what `tools.hot_reload` calls and what an editor's save-loop wants; it goes
        through the same `_sync_skill_tools` the build used, so the registry can hold only one
        shape of skill tool at a time.
        """
        if refresh:
            self.skills.discover(refresh=True)
        report = _sync_skill_tools(self.registry, engine=self.engine, skills=self.skills,
                                  shells=self.shells)
        self.engine.advertise()
        return report

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
    # the runner is attached, not just passed to builtin.register: `refresh_profile` hands its
    # new profile to whatever is attached, and a runner that was never attached kept probing
    # with the build-time snapshot forever (shell.run would ignore a re-detect).
    engine.attach(search_backends=SearchBackend(sandbox, profile), shells=shells)

    # publish store: user-level by default (outside the workspace roots, so the
    # fs sandbox cannot reach it). Override via [publish] store_path.
    store_path = cfg.publish.store_path or os.path.join(_user_dir(), "publish", "store.json")
    publish_store = PublishStore(store_path)
    report["publish_store"] = str(publish_store.path)

    # 1. built-ins
    rep = builtin.register(registry, engine=engine, shells=shells, fs=fs, journal=journal,
                           publish=publish_store)
    report["builtin"] = rep
    # 2. skills -> tool manifests (declarative), and the skills.* tools themselves
    skills = SkillLoader(cfg.skills.dirs, max_body_bytes=cfg.skills.max_body_bytes,
                             profile=profile, respect_priority=cfg.skills.respect_priority,
                             max_inline_tokens=cfg.skills.max_inline_tokens)
    engine.attach(skills=skills)
    _register_skill_tools(registry, engine=engine, skills=skills, shells=shells)
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
    # 4. remote MCP servers (P5b, ADR-0013): explicit [mcp.remotes.<name>] only.
    # A failed server is a load_error + build-report row - never a silent absence.
    report["remote"] = {"servers": [], "registered": [], "errors": []}
    if cfg.mcp.remotes:
        try:
            from .mcp.client import RemoteConnector

            report["remote"] = RemoteConnector(cfg.mcp.remotes).enroll(registry)
        except Exception as exc:  # config-level failure: visible, never a crash
            report["remote"]["errors"].append(
                {"error": f"{type(exc).__name__}: {exc}", "stage": "config"})
            registry.load_errors.append({"stage": "remote", "error": str(exc)[:400]})
    report["registered_after_load"] = len(registry.all())
    return Toolkit(config=cfg, profile=profile, sandbox=sandbox, fs=fs, journal=journal, shells=shells,
                   registry=registry, engine=engine, skills=skills, ledger=ledger,
                   publish=publish_store, build_report=report)


def _mk_overrides(overrides: dict[str, Any] | None, roots: list[str] | None,
                  read_only: bool | None) -> dict[str, Any]:
    out = dict(overrides or {})
    if roots:
        out["roots"] = list(roots)
    if read_only is not None:
        out["policy"] = {**(out.get("policy") or {}), "read_only": read_only}
    return out


def _register_skill_tools(reg: Registry, *, engine: Engine, skills: SkillLoader,
                          shells: Any = None) -> None:
    from .core.manifest import ToolManifest

    def skills_list(refresh: bool = False) -> dict[str, Any]:
        found = skills.discover(refresh=refresh)
        # a skill's parse notes and its failed `[[tool]]` compilations belong in the same
        # report: "the skill did not load" and "the skill loaded but offers nothing" must not be
        # two different places to look
        errors = [*skills.errors, *skills.tool_errors]
        skill_tools = sorted(m.id for m in reg.all() if str(m.source).startswith("skill:"))
        return {"skills": [s.to_dict() for s in found], "count": len(found),
                "dirs": skills.dirs, "errors": errors, "skill_tools": skill_tools,
                "compiled": max(0, len(skill_tools) - len(errors)),
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
        tier="core",
        input_schema={"type": "object", "properties": {"refresh": {"type": "boolean", "default": False}},
                      "additionalProperties": False}), skills_list)
    reg.register(ToolManifest(
        id="skills.load", title="Load a skill",
        description="Return a skill's instruction body (budgeted) plus optional reference files, ready to "
                    "inject into context.",
        capability="skills.load", risk="none", typical_latency_ms=8, tags=["skills", "read", "instructions"],
        tier="core",
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
        tier="core",
        input_schema={"type": "object", "properties": {"task": {"type": "string", "minLength": 1},
                                                        "limit": {"type": "integer", "minimum": 1, "maximum": 10,
                                                                  "default": 3},
                                                        "max_tokens": {"type": "integer", "minimum": 200,
                                                                      "maximum": 8000,
                                                        "default": skills.max_inline_tokens * 3,
                                                        "description": "block budget; defaults to "
                                                                       "skills.max_inline_tokens x limit"}},
                    "required": ["task"], "additionalProperties": False}), skills_match)

    _sync_skill_tools(reg, engine=engine, skills=skills, shells=shells)

    allow_install = bool(getattr(engine.config.skills, "allow_install", False))

    def skills_install(dir: str | None = None, git_ref: str | None = None,
                       name: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        from .core.errors import E as _E
        from .core.errors import SkeletonKeyError as _S
        from .skills import install as _install

        if git_ref:
            raise _S(
                _E.NOT_IMPLEMENTED, "installing from a git ref is not built",
                details={"phase": "P6 (distribution)", "git_ref": str(git_ref)[:160],
                         "why": "a network fetch and a trust decision belong with signing and "
                                "review, not with the file copy"},
                next_actions=[{"tool": "skills.install", "args": {"dir": "<a checkout on disk>",
                                                                  "dry_run": True},
                               "why": "clone it, read the diff, then install the directory"}])
        if not dir:
            raise _S(_E.MISSING_ARG, "pass `dir` (a skill pack on disk) or `git_ref`",
                     details={"args": {"dir": dir, "git_ref": git_ref},
                              "minimal_example": {"dir": "/path/to/my-skill", "dry_run": True}})

        root = (getattr(engine.config.skills, "install_root", "")
                or (engine.skills.dirs[0] if engine.skills.dirs else "skills"))
        root = os.path.abspath(os.path.expanduser(root))
        try:
            plan = _install.plan(str(dir), skills_root=root, name=name, engine=engine,
                                 loader=skills)
        except OSError as exc:
            raise _S(_E.IO, f"cannot read {dir}: {exc.strerror or exc}",
                     details={"dir": str(dir)[:200]}) from None
        if not plan.ok:
            raise _S(
                _E.BAD_ARGS, f"{dir} cannot be installed as a skill",
                details={"blockers": plan.blockers, "warnings": plan.warnings,
                         "plan": plan.to_dict(),
                         "advice": "a skill pack is a directory with SKILL.md at its top level; "
                                   "see docs/SKILLS-SPEC.md"},
                next_actions=[{"tool": "fs.write",
                               "args": {"path": os.path.relpath(os.path.join(plan.src, "SKILL.md"),
                                                                os.getcwd()),
                                        "content": "---\nname: ...\ndescription: ...\n---\n"},
                               "why": "author the pack in the workspace, then install from there"}])
        report = plan.to_dict()
        if dry_run:
            return {**report, "installed": False, "dry_run": True,
                    "note": "nothing was written; re-run with dry_run=false to install"}
        if not allow_install:
            raise _S(
                _E.DENY_RULE, "skills.install is disabled by configuration",
                details={"setting": "skills.allow_install", "current": False,
                         "would_install": report,
                         "why": "a skill brings a script the toolkit will run; PLAN.md keeps this "
                                "off until the policy engine (P3) can scope it",
                         "how_to_enable": "set skills.allow_install = true in skeletonkey.toml"},
                next_actions=[{"tool": "skills.install",
                               "args": {"dir": str(dir), "dry_run": True},
                               "why": "dry_run answers even while the gate is closed, to review the plan"}])
        written = _install.commit(plan, fs=engine.fs, task_id=f"skill-install:{plan.name}")
        skills.discover(refresh=True)
        sync = _sync_skill_tools(reg, engine=engine, skills=skills, shells=shells)
        return {**report, "installed": True, "dry_run": False, "written": written["written"],
                "tools": {"added": sync["added"], "updated": sync["updated"]},
                "errors": sync["errors"],
                "undo": {"tool": "fs.undo_task", "args": {"task_id": f"skill-install:{plan.name}"},
                         "why": "the copy is journalled, so the files can go back"},
                "hints": [f"{len(sync['advertised'])} skill-authored tool(s) are now advertised"]
                if sync["advertised"] else ["the skill declares no runnable tools; skills.list "
                                            "shows what it added"]}

    def skills_uninstall(name: str, remove_files: bool = True,
                         dry_run: bool = False) -> dict[str, Any]:
        from .core.errors import E as _E
        from .core.errors import SkeletonKeyError as _S
        from .skills import install as _install

        skill = skills.get(str(name))
        report = _install.plan_uninstall(skill, registry=reg)
        jobs = [j for j in (shells.jobs() if shells is not None else [])
                if j.get("running") and str(j.get("owner") or "").endswith(f"skill:{skill.name}")]
        if jobs and not dry_run:
            raise _S(
                _E.CONFLICT, f"{skill.name} has {len(jobs)} job(s) still running",
                details={"jobs": jobs, "skill": skill.name,
                         "advice": "the script a running job inlined lives in this directory; "
                                    "wait for it or kill it first"},
                next_actions=[*[{"tool": "shell.job_kill", "args": {"job_id": j["job_id"]}}
                               for j in jobs[:5]],
                              {"tool": "shell.job_wait",
                               "args": {"job_id": jobs[0]["job_id"], "timeout_s": 30},
                               "why": "or wait for the one job that matters"}])
        out: dict[str, Any] = {**report, "dry_run": bool(dry_run), "skill": skill.name,
                              "uninstalled": False}
        if dry_run:
            return {**out, "note": "nothing removed; re-run with dry_run=false"}
        # the delete goes to the Fs object, not to engine.call("fs.delete"): one approval for
        # one action, and this tool already carries the destructive risk that approval is for
        removed = [tid for tid in report["tools"] if reg.has(tid)]
        for tid in removed:
            reg.unregister(tid)
        out["tools_removed"] = sorted(removed)
        if remove_files:
            rel = os.path.relpath(skill.path, os.path.abspath(engine.fs.sb.roots[0]))
            rel = rel.replace(os.sep, "/")
            if os.path.commonpath([os.path.abspath(skill.path),
                                   os.path.abspath(engine.fs.sb.roots[0])]) != os.path.abspath(
                                       engine.fs.sb.roots[0]):
                out["warnings"] = [f"{skill.path} is outside the sandbox roots, so its files were "
                                   f"left in place; the tools are unregistered either way"]
            else:
                res = engine.fs.delete(rel, recursive=True, task_id=f"skill-uninstall:{skill.name}")
                out["deleted"] = rel
                tok = res.get("undo_token") if isinstance(res, dict) else None
                if tok:
                    out["undo"] = {"tool": "fs.undo", "args": {"token": tok}}
        skills.discover(refresh=True)
        sync = _sync_skill_tools(reg, engine=engine, skills=skills, shells=shells)
        out["uninstalled"] = True
        out["sync"] = {"added": sync["added"], "removed": sync["removed"],
                        "errors": sync["errors"]}
        out["hints"] = ["re-run registry.list/tools/list to see the smaller surface"]
        return out

    reg.register(ToolManifest(
        id="skills.install", title="Install a skill pack",
        description="Copy a reviewed skill directory into a skills root and compile its declared "
                    "tools in this process - no restart. Refused unless skills.allow_install is true; "
                    "dry_run answers either way, so the plan can be reviewed first.",
        capability="skills.install", group="skills", risk="write", reversible=True, tier="task",
        typical_latency_ms=90, tags=["skills", "install", "dynamic", "registry"], timeout_s=60,
        advertised=allow_install,
        hidden_reason=None if allow_install else "skills.allow_install is false, so every call "
                                                 "would only refuse; enable it to advertise this",
        anti_patterns=["do not install a skill pack you have not read - it is code the toolkit runs",
                       "do not point dir at a large tree; the file caps refuse it for good reason"],
        see_also=["skills.uninstall", "skills.list", "fs.undo", "registry.describe"],
        examples=[{"args": {"dir": "/tmp/my-skill", "dry_run": True},
                   "note": "review the plan before writing anything"}],
        input_schema={
            "type": "object",
            "properties": {
                "dir": {"type": "string",
                        "description": "A skill pack on disk: a directory with SKILL.md at its top level."},
                "git_ref": {"type": "string",
                            "description": "Not built in P2; refused with the manual path to follow."},
                "name": {"type": "string",
                         "description": "Install under this name instead of the directory's basename."},
                "dry_run": {"type": "boolean", "default": False,
                            "description": "Validate and report only: files, tool ids, requirements, argv."}},
            "additionalProperties": False}), skills_install)

    reg.register(ToolManifest(
        id="skills.uninstall", title="Remove a skill pack",
        description="Unregister a skill's tools and, by default, delete its directory through the "
                    "journal. Not gated by skills.allow_install - removing capability is not "
                    "escalating it - but it is marked destructive so the same approval policy "
                    "that guards fs.delete guards it. Refuses while a job from that skill is "
                    "still running.",
        capability="skills.uninstall", group="skills", risk="destructive", destructive=True,
        tier="task",
        reversible=True, approval="policy",
        typical_latency_ms=70, tags=["skills", "uninstall", "dynamic", "registry"], timeout_s=60,
        anti_patterns=["do not uninstall to change a script; edit the file and let the reload do it"],
        see_also=["skills.install", "skills.list", "fs.undo", "shell.jobs"],
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1,
                                    "description": "The skill's name, as skills.list reports it."},
                           "remove_files": {"type": "boolean", "default": True,
                                            "description": "false unregisters the tools and leaves the directory"},
                           "dry_run": {"type": "boolean", "default": False}},
            "required": ["name"], "additionalProperties": False}), skills_uninstall)


# ---------------------------------------------------------------- skill -> tool registration
def _sync_skill_tools(reg: Registry, *, engine: Engine, skills: SkillLoader,
                      shells: Any = None, force_replace: bool = False) -> dict[str, Any]:
    """Compile every skill's `[[tool]]` declarations into the registry, in place.

    One code path serves build time, `skills.install`, `skills.uninstall` and the file watcher,
    so "it worked after installing and broke after restarting" cannot happen: same compiler,
    same refusals, same error report. Returns the delta it applied.
    """
    from .core.manifest import ToolManifest
    from .skills.compiler import SkillToolError, compile_tool

    cfg = engine.config
    allow_override = force_replace or bool(getattr(getattr(cfg, "tools", None),
                                                   "override_builtin", False))
    prefer_windows = str(getattr(engine.profile, "os", "") or "").lower() == "windows"
    wanted: set[str] = set()
    added: list[str] = []
    updated: list[str] = []
    errors: list[dict[str, Any]] = []
    # a skill may not shadow a built-in unless the operator asked for it; two skills claiming the
    # same id is caught because `known` grows as we go
    known = {m.id for m in reg.all() if not str(m.source).startswith("skill:")}

    for skill, decl in skills.tool_declarations():
        tool_id = (str(decl.get("id") or "").strip()
                   or f"skill.{skill.name}.{decl.get('name') or 'tool'}")
        wanted.add(tool_id)
        is_new = not reg.has(tool_id)
        if not (decl.get("handler_script") or decl.get("handler_body")):
            try:
                man = ToolManifest.from_dict(
                    {**decl, "id": tool_id, "group": decl.get("group") or f"skill.{skill.name}",
                     "advertised": decl.get("advertised", True)},
                    source=f"skill:{skill.name}")
                man.meta["declares"] = True
                if not man.description:
                    man.description = ("Declared by a skill with no handler_script or "
                                       "handler_body; calling it reports NOT_IMPLEMENTED.")
                reg.register(man, _unavailable_skill_handler(man.id), replace=not is_new)
                (added if is_new else updated).append(tool_id)
            except Exception as exc:
                errors.append({"skill_tool": tool_id, "stage": "declare", "path": skill.path,
                               "error": str(exc)[:300]})
            continue
        try:
            manifest, binding = compile_tool(skill.name, skill.path, decl, known_ids=known,
                                             override_builtin=allow_override)
            man = ToolManifest.from_dict(manifest, source=f"skill:{skill.name}")
            man.meta["skill"] = skill.name
            man.meta["binding"] = binding.to_dict()
            reg.register(man, _skill_tool_handler(engine, shells, binding,
                                                   prefer_windows=prefer_windows),
                         replace=not is_new or allow_override)
            known.add(tool_id)
            (added if is_new else updated).append(tool_id)
        except SkillToolError as exc:
            errors.append({**exc.to_dict(), "path": skill.path})
        except Exception as exc:  # a manifest that cannot be built is a load error, not a crash
            errors.append({"skill_tool": tool_id, "stage": "compile", "path": skill.path,
                           "error": str(exc)[:300]})

    removed: list[str] = []
    for man in list(reg.all()):
        if str(man.source).startswith("skill:") and man.id not in wanted:
            reg.unregister(man.id)
            removed.append(man.id)

    skills.tool_errors = errors
    # keep the registry-wide view consistent, replacing only the skill rows so a drop-in's
    # errors survive a skills refresh
    reg.load_errors[:] = [e for e in reg.load_errors if "skill_tool" not in e] + errors
    return {"added": sorted(added), "updated": sorted(updated), "removed": sorted(removed),
            "errors": errors,
            "advertised": sorted(m.id for m in reg.all()
                                 if m.advertised and str(m.source).startswith("skill:"))}


def _skill_tool_handler(engine: Engine, shells: Any, binding, *, prefer_windows: bool = False):
    """The handler for a compiled skill tool: one script, run through the shared executor.

    Deliberately *not* `engine.call("shell.run", ...)`: a nested call would ask for approval a
    second time for a capability the caller already approved and write two ledger rows for one
    action - the same argument ADR 0007 makes about quoting, applied to the envelope. The
    budget, the ledger, truncation and the error taxonomy all still apply, because
    `run_script` is the code `shell.run` itself runs.
    """
    def _handler(**args: Any) -> Any:
        dialect = args.pop("dialect", None)
        kwargs = binding.request(args, dialect=dialect, prefer_windows=prefer_windows)
        kwargs["extra_data"] = {**kwargs["extra_data"], "skill_tool": binding.tool_id,
                                "skill_dir": binding.skill_dir}
        return run_script(engine, shells if shells is not None else engine.shells,
                          via=binding.channel, owner=f"skill:{binding.skill}",
                          result_key="result", **kwargs)

    return _handler


def _unavailable_skill_handler(tool_id: str):
    from .core.errors import E as _E
    from .core.errors import SkeletonKeyError as _S

    def _h(**_kw: Any) -> Any:
        raise _S(_E.NOT_IMPLEMENTED, f"skill-declared tool {tool_id!r} has no executor yet",
                 details={"phase": "2 (skill runtime + tool compiler)", "plan": "PLAN.md"},
                 next_actions=[{"tool": "registry.search", "args": {"query": tool_id}}])
    return _h
