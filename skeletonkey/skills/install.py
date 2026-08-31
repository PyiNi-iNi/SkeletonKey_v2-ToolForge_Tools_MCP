"""Install and uninstall skill packs - the only way a skill arrives without a commit.

A skill pack is a directory: `SKILL.md` at the top, optional `references/`, `scripts/`, and an
optional `tool.toml` whose `[[tool]]` tables become executable tools. So "install" is "copy a
directory the operator pointed at into a skills root", and that is the moment the toolkit gains
the ability to run code it has never seen. Two rules follow from that:

* it is **off by default** (`skills.allow_install = false`) and stays off until P3's policy
  engine can scope it; the tool exists either way, so the refusal is explainable instead of
  mysterious;
* the copy goes through the *journal* (`fs.write`/`fs.delete`), so `skills.uninstall` and
  `fs.undo`
  both work on it and nothing lands outside the roots by accident.

Reading a directory is not privileged, so the source may live anywhere; the *destination* is
what must be inside a skills root, and the file list is what a reviewer must be able to read -
hence the small extension allowlist, the size caps, and no symlinks.
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, field
from typing import Any

SKILL_FILE = "SKILL.md"
MANIFEST_FILE = "tool.toml"
COPY_EXT = frozenset({".md", ".toml", ".txt", ".sh", ".bash", ".ps1", ".psm1", ".py"})
MAX_FILES = 24
MAX_FILE_BYTES = 512_000
MAX_TOTAL_BYTES = 2_000_000


@dataclass
class InstallPlan:
    """What an install would do, computed before anything is written."""

    name: str
    src: str
    dest: str
    files: list[tuple[str, str, int]] = field(default_factory=list)   # (rel, abs, bytes)
    tool_ids: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    argv_preview: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    replaces: str | None = None          # existing skill dir this would overwrite

    @property
    def ok(self) -> bool:
        return not self.blockers and bool(self.files)

    def to_dict(self) -> dict[str, Any]:
        total = sum(n for _r, _a, n in self.files)
        return {"skill": self.name, "source": self.src, "dest": self.dest,
                "files": [{"path": rel, "bytes": n} for rel, _a, n in self.files],
                "file_count": len(self.files), "total_bytes": total,
                "tools": self.tool_ids, "requirements": self.requirements,
                "would_run": self.argv_preview, "warnings": self.warnings,
                "blockers": self.blockers, "replaces": self.replaces}


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def scan_source(src: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    """`(files, warnings)` for a candidate skill directory, without trusting it."""
    src = os.path.abspath(os.path.expanduser(src))
    warnings: list[str] = []
    if not os.path.isdir(src):
        raise NotADirectoryError(f"{src} is not a directory")
    if not os.path.isfile(os.path.join(src, SKILL_FILE)):
        raise FileNotFoundError(f"{src} has no {SKILL_FILE}; a skill pack is a directory with "
                                f"{SKILL_FILE} at its top level")
    picked: list[tuple[str, str, int]] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in {"__pycache__", "node_modules",
                                                                    ".git"})
        for name in sorted(filenames):
            abs_path = os.path.join(dirpath, name)
            rel = _rel(abs_path, src)
            if name.startswith("."):
                continue
            if os.path.islink(abs_path):
                warnings.append(f"skipped symlink {rel} - a skill cannot smuggle in a path "
                                f"outside its directory")
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in COPY_EXT:
                warnings.append(f"skipped {rel}: {ext or 'no extension'} is not a skill file type")
                continue
            try:
                size = os.path.getsize(abs_path)
            except OSError as exc:
                warnings.append(f"skipped {rel}: {exc}")
                continue
            if size > MAX_FILE_BYTES:
                warnings.append(f"skipped {rel}: {size} bytes exceeds the {MAX_FILE_BYTES} "
                                f"per-file cap")
                continue
            if len(picked) >= MAX_FILES:
                warnings.append(f"stopped at {MAX_FILES} files; the source has more, which is not "
                                "a skill pack shape")
                break
            if total + size > MAX_TOTAL_BYTES:
                warnings.append(f"stopped: the pack exceeds the {MAX_TOTAL_BYTES} byte budget")
                break
            total += size
            picked.append((rel, abs_path, size))
        if len(picked) >= MAX_FILES:
            break
    return picked, warnings


def plan(src: str, *, skills_root: str, name: str | None = None,
         engine: Any = None, loader: Any = None) -> InstallPlan:
    """Validate and describe an install. Nothing is written; nothing can be."""
    src_abs = os.path.abspath(os.path.expanduser(src))
    skill_name = (name or os.path.basename(src_abs.rstrip("/")) or "").strip()
    dest = os.path.join(os.path.abspath(skills_root), skill_name)
    plan_ = InstallPlan(name=skill_name, src=src_abs, dest=dest)
    if not skill_name or skill_name in {".", ".."} or not _safe_name(skill_name):
        plan_.blockers.append(f"skill name {skill_name!r} is not usable as a directory name")
        return plan_
    if posixpath.normpath(dest) == posixpath.normpath(src_abs):
        plan_.blockers.append("the source already is the install destination")
        return plan_
    try:
        files, warnings = scan_source(src_abs)
    except (NotADirectoryError, FileNotFoundError) as exc:
        plan_.blockers.append(str(exc))
        return plan_
    plan_.files = files
    plan_.warnings = warnings
    if os.path.isdir(dest):
        plan_.replaces = dest
        plan_.warnings.append(f"{dest} already exists - installing replaces its files")
    if not any(rel == SKILL_FILE for rel, _a, _n in files):
        plan_.blockers.append(f"{SKILL_FILE} was not among the copyable files")

    # compile the declarations now, so a broken skill is refused before it lands rather than
    # discovered by the first agent that trusts it
    manifest_path = os.path.join(src_abs, MANIFEST_FILE)
    if os.path.isfile(manifest_path):
        from .compiler import SkillToolError, compile_tool

        try:
            with open(manifest_path, encoding="utf-8") as fh:
                raw = fh.read()
            decls = _declarations(raw, plan_)
        except OSError as exc:
            plan_.blockers.append(f"{MANIFEST_FILE} unreadable: {exc}")
            decls = []
        known = {m.id for m in engine.registry.all()} if engine is not None else set()
        allow_override = bool(getattr(getattr(engine.config, "tools", None), "override_builtin",
                                      False)) if engine is not None else False
        for decl in decls:
            if not (decl.get("handler_script") or decl.get("handler_body")):
                tool_id = str(decl.get("id") or f"skill.{skill_name}.{decl.get('name', 'tool')}")
                plan_.tool_ids.append(tool_id)
                plan_.warnings.append(f"{tool_id} declares no script, so it installs as a stub "
                                      "that reports NOT_IMPLEMENTED")
                continue
            try:
                manifest, binding = compile_tool(skill_name, src_abs, decl, known_ids=known,
                                                 override_builtin=allow_override)
            except SkillToolError as exc:
                plan_.blockers.append(exc.reason + (f" ({exc.field})" if exc.field else ""))
                plan_.warnings.append(f"advice: {exc.advice}") if exc.advice else None
                continue
            plan_.tool_ids.append(manifest["id"])
            for req in manifest.get("requires") or []:
                plan_.requirements.append(str(req))
            plan_.argv_preview.append(_preview(binding, skill_dir=dest,
                                              schema=manifest.get("input_schema") or {}))
    if loader is not None:
        for existing in loader.discover():
            if existing.name == skill_name and os.path.abspath(existing.path) != dest:
                plan_.warnings.append(f"a skill named {skill_name} is already loaded from "
                                      f"{existing.path}; the newly installed one wins only if it "
                                      "sorts first - rename to keep both")
    return plan_


def _declarations(raw: str, plan_: InstallPlan) -> list[dict[str, Any]]:
    try:
        import tomllib
    except ImportError:                                            # pragma: no cover - py<3.11
        plan_.blockers.append("tool.toml needs python 3.11+ (tomllib) to be validated")
        return []
    try:
        data = tomllib.loads(raw)
    except ValueError as exc:
        plan_.blockers.append(f"{MANIFEST_FILE} is not valid TOML: {exc}")
        return []
    tools = data.get("tool")
    if isinstance(tools, dict):
        tools = [tools]
    if not isinstance(tools, list):
        tools = data.get("tools") if isinstance(data.get("tools"), list) else []
    return [t for t in tools if isinstance(t, dict)]


def _preview(binding, *, skill_dir: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The argv this tool would run, as far as it can be known without a host probe."""
    from dataclasses import replace as _replace

    # the preview describes the *installed* skill, whose scripts live in the destination
    shown = _replace(binding, skill_dir=skill_dir)
    names = sorted(k for k in (schema.get("properties") or {}) if k != "dialect")
    sample_args = dict.fromkeys(names, "<value>")
    req = shown.request(sample_args, prefer_windows=False)
    sample = {"interpreter": f"<the {req['dialect']} binary, resolved at run time>",
              "channel": binding.channel,
              "cwd": "<workspace root>",
              "env": dict(req.get("env") or {}),
              "env_mode": req["env_mode"],
              "timeout_s": req["timeout_s"],
              "expects": binding.expects,
              "script": ("<inlined from " + binding.script_rel + ">") if binding.script_rel
              else "<inlined handler_body>"}
    if req.get("argv"):
        sample["payload_argv"] = req["argv"]
    return sample


def targets(plan_: InstallPlan, fs: Any) -> list[tuple[str, str]]:
    """`(relative_target, absolute_source)` pairs, sandbox-checked *before* anything is written.

    The destination has to be inside a root for `fs.write` to accept it, and "the skills dir
    lives outside the workspace" is an operator error that deserves naming rather than a
    SANDBOX_VIOLATION on the third file of a half-written install.
    """
    from ..core.errors import E, SkeletonKeyError

    out: list[tuple[str, str]] = []
    for rel, abs_src, _n in plan_.files:
        target = os.path.relpath(os.path.join(plan_.dest, rel), os.path.abspath(fs.sb.roots[0]))
        target = target.replace(os.sep, "/")
        try:
            fs.sb.resolve(target, intent="write")
        except SkeletonKeyError as exc:
            if exc.code == "SANDBOX_VIOLATION":
                raise SkeletonKeyError(
                    E.SANDBOX_VIOLATION, f"the install destination is outside the sandbox: "
                                        f"{os.path.join(plan_.dest, rel)}",
                    details={"path": target, "dest_root": plan_.dest,
                             "roots": list(fs.sb.roots),
                             "advice": "point skills.install_root at a directory inside `roots`, "
                                       "or add the skills directory to `roots`"},
                ) from None
            raise
        out.append((target, abs_src))
    return out


def commit(plan_: InstallPlan, *, fs: Any, task_id: str = "") -> dict[str, Any]:
    """Write the planned files through `fs`, so the journal can undo an install."""
    written: list[str] = []
    tokens: list[str] = []
    for target, abs_src in targets(plan_, fs):
        with open(abs_src, encoding="utf-8", errors="surrogateescape") as fh:
            body = fh.read()
        res = fs.write(target, body, create_dirs=True, task_id=task_id)
        written.append(target)
        tok = getattr(res, "undo_token", None)
        if tok:
            tokens.append(str(tok))
    return {"written": written, "undo_tokens": tokens}


def plan_uninstall(skill: Any, *, registry: Any) -> dict[str, Any]:
    """What removing one skill takes away: its directory and the tools compiled from it."""
    prefix = f"skill:{skill.name}"
    tools = [m.id for m in registry.all() if str(getattr(m, "source", "")).startswith(prefix + "")]
    return {"skill": skill.name, "dir": skill.path, "tools": sorted(tools),
            "files": _dir_listing(skill.path)}


def _dir_listing(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            out.append(_rel(os.path.join(dirpath, name), root))
        if len(out) > 200:                                        # pragma: no cover - guard
            break
    return out


def _safe_name(name: str) -> bool:
    if len(name) > 64 or name != name.strip():
        return False
    return all(ch.isalnum() or ch in "-._" for ch in name) and name[0].isalnum()


def skill_dir_hint(loader: Any) -> str:
    """Where installs go: the configured root, or the first skills dir."""
    dirs = list(getattr(loader, "dirs", []) or [])
    return dirs[0] if dirs else "skills"
