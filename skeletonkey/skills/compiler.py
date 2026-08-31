"""Compile a skill's `tool.toml` declaration into a callable tool.

A skill-authored tool is **a manifest plus a subprocess**, never executed Python: the sandbox,
the budget and the journal keep applying unchanged, and a broken script fails as `NONZERO_EXIT`
with the tail attached instead of corrupting the process that ran it.

The binding surface is three channels, and that ceiling is the whole design:

``flags``       every provided property becomes ``--flag-name <value>`` in *argv* (bools are
                the bare flag, arrays repeat the flag). Default.
``argv_json``   the whole args object is ``json.dumps``'d into **one** argv element; the script
                reads ``$1`` / ``$args[0]`` / ``sys.argv[1]``.
``stdin_json``  the args object goes to stdin as ``stdin_text``; nothing in argv.
``none``        the script takes no input (a probe, like the selftest this was built for).

`dialect` is reserved: a property with that name is consumed by the compiler as the caller's
choice of interpreter and never reaches the script. A declaration that pins `dialect` (because
the handler body is written in one language) therefore may not also declare the property - the
two would fight, and the arg would win.

What is *not* supported, on purpose: putting a caller's value into the script text. A
declaration that writes ``{path}``-style placeholders gets refused with the channel list,
because that is how a "dynamic toolset" turns into an ``eval`` over model-authored strings -
and every value here rides in argv, where no shell parser ever sees it (ADR 0007).
``$ARG_json`` in a `handler_body` names the *channel*, not a value, so it is the one token
the compiler substitutes (with the dialect's own positional reference).
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

CHANNELS = ("flags", "argv_json", "stdin_json", "none")
EXPECTS = ("json", "lines", "text")
ALLOWED_EXT = frozenset({".sh", ".bash", ".ps1", ".psm1", ".py"})
ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
PROP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
FLAG_RE = re.compile(r"^--[a-z0-9][a-z0-9-]*$")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")
ARG_JSON = "$ARG_json"

# Dialect families that read their first positional differently. `$ARG_json` is replaced by the
# reference, never by the value, so quoting stays the OS's problem rather than ours.
_FIRST_ARG = {"posix": '"$1"', "powershell": "$args[0]", "python": "sys.argv[1]"}
_POSIX_DIALECTS = ("bash", "sh", "zsh", "fish")
_WINDOWS_DIALECTS = ("pwsh", "powershell")


class SkillToolError(Exception):
    """A declaration that cannot become a tool. Carries the stage so `load_errors` stays legible."""

    def __init__(self, tool_id: str, reason: str, *, advice: str = "", field: str = "") -> None:
        super().__init__(f"{tool_id}: {reason}")
        self.tool_id = tool_id
        self.stage = "compile"
        self.reason = reason
        self.advice = advice
        self.field = field

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"skill_tool": self.tool_id, "stage": self.stage,
                               "error": self.reason}
        if self.advice:
            out["advice"] = self.advice
        if self.field:
            out["field"] = self.field
        return out


@dataclass
class SkillToolBinding:
    """Everything needed to turn validated args into one `shell.run`-shaped request."""

    tool_id: str
    skill: str
    skill_dir: str
    channel: str = "flags"
    expects: str = "text"
    env_mode: str = "clean"
    timeout_s: float = 120.0
    dialect: str | None = None
    script_rel: str = ""
    script_abs: str = ""
    windows_script_abs: str | None = None
    body: str | None = None                       # inline handler_body, when there is one
    flags: dict[str, str] = field(default_factory=dict)
    consumed: tuple[str, ...] = ("dialect",)      # properties the compiler uses itself

    # ------------------------------------------------------------------ execution
    def dialect_for(self, requested: str | None, *, prefer_windows: bool) -> str:
        """The dialect this call runs in: explicit arg, else declared, else the host's."""
        for cand in (requested, self.dialect):
            if cand:
                return str(cand)
        return _WINDOWS_DIALECTS[0] if prefer_windows else _POSIX_DIALECTS[0]

    def script_text(self, dialect: str) -> str:
        """The script body for `dialect`.

        A file-based skill script is read and **inlined** rather than invoked by path: skill
        directories are usually outside the sandbox roots, so `cd`-ing there to run it would
        either be refused or would quietly widen the roots. Inlining keeps the payload inside
        the runner's own temp dir, and `keep_script` then leaves the exact text on disk.
        """
        if self.body is not None:
            fam = family_for(dialect)
            if ARG_JSON in self.body:
                return self.body.replace(ARG_JSON, _FIRST_ARG[fam])
            return self.body
        chosen = self.script_abs
        if family_for(dialect) == "powershell" and self.windows_script_abs:
            chosen = self.windows_script_abs
        try:
            with open(chosen, encoding="utf-8-sig") as fh:   # a BOM would become part of the body
                return fh.read()
        except OSError as exc:                                 # pragma: no cover - checked at compile
            raise FileNotFoundError(f"{chosen}: {exc}") from exc

    def request(self, args: dict[str, Any], *, dialect: str | None = None,
                prefer_windows: bool = False) -> dict[str, Any]:
        """Kwargs for `shells.execute.run_script` — the single point where args become a call."""
        dl = self.dialect_for(dialect, prefer_windows=prefer_windows)
        rest = {k: v for k, v in args.items() if k not in self.consumed}
        argv: list[str] = []
        stdin_text: str | None = None
        if self.channel == "flags":
            argv = _flag_argv(rest, self.flags)
        elif self.channel == "argv_json":
            argv = [json.dumps(rest, sort_keys=True)]
        elif self.channel == "stdin_json":
            stdin_text = json.dumps(rest, sort_keys=True)
        # env tells the script where its skill lives; under env_mode=clean it is the only
        # thing from the outside world that arrives, and the prefix keeps it through.
        env = {"SKELETONKEY_SKILL": self.skill, "SKELETONKEY_SKILL_DIR": self.skill_dir}
        return {"script": self.script_text(dl), "dialect": dl, "argv": argv or None,
                "stdin_text": stdin_text, "env": env, "env_mode": self.env_mode,
                "expects": self.expects if self.expects != "text" else None,
                "timeout_s": self.timeout_s, "keep_script": False,
                "extra_data": {"skill": self.skill, "script": self.script_rel or "<handler_body>",
                               "args_via": self.channel}}

    def to_dict(self) -> dict[str, Any]:
        """What `registry.describe`/`skills.list`/an install dry-run report about the binding."""
        return {"tool": self.tool_id, "skill": self.skill, "channel": self.channel,
                "expects": self.expects, "env_mode": self.env_mode, "timeout_s": self.timeout_s,
                "dialect": self.dialect, "script": self.script_rel or None,
                "script_windows": bool(self.windows_script_abs), "flags": dict(self.flags),
                "inline_body": self.body is not None}


def family_for(dialect: str) -> str:
    d = (dialect or "").lower()
    if d in _WINDOWS_DIALECTS or "powershell" in d or d.startswith("pwsh"):
        return "powershell"
    if d.startswith("python"):
        return "python"
    return "posix"


def _string_list(decl: dict[str, Any], key: str, tool_id: str) -> list[str]:
    """`tags` / `see_also` / `anti_patterns` are lists of plain strings (docs/TOOL-CONTRACT.md).

    Worth refusing at compile time: these lists get joined into the advertisement text, so a
    table where a string was expected surfaces far away - as a crash inside `tools/list`.
    """
    raw = decl.get(key)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, (list, tuple)):
        raise SkillToolError(tool_id, f"{key} must be a list of strings", field=key)
    bad = [v for v in raw if not isinstance(v, str)]
    if bad:
        raise SkillToolError(
            tool_id, f"{key} holds {len(bad)} non-string item(s)", field=key,
            advice=f"{key} is joined into the advertised description, so each entry must be one "
                   "sentence of text - a table with do/why/instead keys is not accepted here")
    return [str(v) for v in raw]


def _flag_argv(rest: dict[str, Any], flags: dict[str, str]) -> list[str]:
    """Values become argv entries, never script text. That is the entire quoting story."""
    argv: list[str] = []
    for name, value in rest.items():
        if value is None:
            continue
        flag = flags.get(name) or f"--{name.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                argv.append(flag)
            continue
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            argv.extend([flag, item if isinstance(item, str) else json.dumps(item)
                         if isinstance(item, (dict, list, bool)) else str(item)])
    return argv


def compile_tool(skill_name: str, skill_dir: str, decl: dict[str, Any], *,
                 known_ids: set[str] | None = None, override_builtin: bool = False,
                 default_timeout_s: float = 120.0) -> tuple[dict[str, Any], SkillToolBinding]:
    """Validate one `[[tool]]` table.

    Returns the manifest kwargs to register and the binding the handler will use. Raises
    `SkillToolError` for anything that would produce a callable-but-broken tool - the failure
    belongs at load time, where `skills.list {errors}` can show it, not in an agent's face.
    """
    if not isinstance(decl, dict):
        raise SkillToolError(str(decl), "declaration is not a table")
    name = str(decl.get("name") or "").strip()
    tool_id = str(decl.get("id") or "").strip()
    if not tool_id:
        if not name:
            raise SkillToolError(decl.get("id", "?"), "declares neither id nor name")
        tool_id = f"skill.{skill_name}.{name.replace('_', '-')}"
    if not ID_RE.match(tool_id):
        raise SkillToolError(tool_id, f"id {tool_id!r} is not a lowercase dotted id",
                             field="id", advice="use letters, digits, '.', '-' and '_'")
    if name and not PROP_RE.match(name):
        raise SkillToolError(tool_id, f"tool name {name!r} is not usable", field="name")

    known = known_ids or set()
    if tool_id in known and not override_builtin:
        raise SkillToolError(
            tool_id, f"{tool_id} is already registered and skills may not shadow it",
            field="id", advice="rename the tool, or set tools.override_builtin = true if you "
                               "mean to replace the implementation for this profile")

    schema = decl.get("input_schema")
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError as exc:
            raise SkillToolError(tool_id, f"input_schema is not valid JSON: {exc}",
                                 field="input_schema",
                                 advice="a TOML `\"\"\"` string keeps its line breaks and JSON does "
                                        "not allow a raw newline inside a string - keep each value "
                                        "on one line") from exc
    if schema is None:
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillToolError(tool_id, "input_schema must be an object schema", field="input_schema")
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        raise SkillToolError(tool_id, "input_schema.properties must be a table", field="input_schema")
    for prop in props:
        if not PROP_RE.match(str(prop)):
            raise SkillToolError(
                tool_id, f"property {prop!r} cannot be bound to an argument", field="input_schema",
                advice="property names become flags: letters, digits and '_' only, starting with "
                       "a letter")

    script_rel = decl.get("handler_script")
    body = decl.get("handler_body")
    if script_rel and body:
        raise SkillToolError(tool_id, "declares both handler_script and handler_body",
                             advice="pick one; a file is the normal choice")
    if not script_rel and not body:
        raise SkillToolError(
            tool_id, "no handler_script or handler_body, so it stays a declaration only",
            advice="add handler_script = \"scripts/x.sh\" (relative to the skill directory)")
    if not isinstance(script_rel, str) and script_rel:
        raise SkillToolError(tool_id, "handler_script must be a string", field="handler_script")

    script_abs = ""
    windows_abs: str | None = None
    if script_rel:
        script_abs, script_rel = _skill_relative(skill_dir, str(script_rel), tool_id)

    channel = str(decl.get("args_via") or decl.get("channel") or "flags")
    if channel not in CHANNELS:
        raise SkillToolError(tool_id, f"args_via {channel!r} is not a binding",
                             field="args_via", advice="one of: " + ", ".join(CHANNELS))
    used = {k for k in props if k != "dialect"}
    if channel == "none" and used:
        raise SkillToolError(
            tool_id, f"args_via = \"none\" but the schema declares {sorted(used)}",
            field="args_via", advice="those properties would be silently dropped; use flags, "
                                     "argv_json or stdin_json")
    if channel == "flags" and any(str(p) == "stdin" for p in props):
        raise SkillToolError(tool_id, "a property named `stdin` collides with the stdin channel",
                             field="input_schema")

    if body is not None:
        if not isinstance(body, str) or not body.strip():
            raise SkillToolError(tool_id, "handler_body must be a non-empty string",
                                 field="handler_body")
        # only a placeholder naming a *declared property* is interpolation; an f-string's
        # `{x}` in a python body is that language's own syntax and none of our business
        stray = sorted({m for m in PLACEHOLDER_RE.findall(body) if m in props})
        if stray:
            raise SkillToolError(
                tool_id, f"handler_body interpolates {stray} into the script text",
                field="handler_body",
                advice="values go in argv: use args_via = flags (default), or $ARG_json with "
                       "argv_json / stdin_json - never a {placeholder}")
        if ARG_JSON in body and channel != "argv_json":
            raise SkillToolError(
                tool_id, f"{ARG_JSON} appears in handler_body but args_via = {channel!r}",
                field="args_via", advice="the marker means \"read the JSON argument\", so it "
                                        "requires args_via = \"argv_json\"")
        if ARG_JSON not in body and channel == "argv_json" and used:
            raise SkillToolError(
                tool_id, "args_via = \"argv_json\" but the body never reads the argument",
                field="handler_body", advice=f"reference {ARG_JSON} where the script should read it")

    if decl.get("dialect") and "dialect" in props:
        raise SkillToolError(
            tool_id, "declares both a pinned dialect and a `dialect` property", field="dialect",
            advice="`dialect` in the schema is the caller choosing the interpreter, so it would "
                   "override the pinned one; rename the property (target_dialect) if the script "
                   "needs to know what text it is looking at")

    expects = str(decl.get("expects") or "text")
    if expects not in EXPECTS:
        raise SkillToolError(tool_id, f"expects {expects!r} is not a contract", field="expects",
                             advice="one of: " + ", ".join(EXPECTS))

    env_mode = str(decl.get("env_mode") or "clean")
    if env_mode not in ("clean", "inherit", "login"):
        raise SkillToolError(tool_id, f"env_mode {env_mode!r} does not exist", field="env_mode",
                             advice="clean (default) | inherit | login")

    flags_map: dict[str, str] = {}
    declared_flags = decl.get("flags") or {}
    if not isinstance(declared_flags, dict):
        raise SkillToolError(tool_id, "flags must be a table of property = flag", field="flags")
    for prop, flag in declared_flags.items():
        if prop not in props:
            raise SkillToolError(tool_id, f"flags mentions unknown property {prop!r}",
                                 field="flags")
        if not FLAG_RE.match(str(flag)):
            raise SkillToolError(tool_id, f"flag {flag!r} for {prop} is not a long flag",
                                 field="flags", advice="lowercase `--like-this`")
        flags_map[str(prop)] = str(flag)

    for key in ("tags", "see_also", "anti_patterns"):
        _string_list(decl, key, tool_id)

    risk = str(decl.get("risk") or "write")
    if decl.get("destructive"):
        raise SkillToolError(
            tool_id, "a skill-authored tool cannot declare destructive = true",
            field="destructive",
            advice="P2 keeps the ceiling at write; a skill that deletes goes through fs.* tools "
                   "(journal included), or waits for P3's policy engine to scope it")
    if risk not in ("none", "read", "write"):
        raise SkillToolError(tool_id, f"risk {risk!r} is above what a skill tool may claim",
                             field="risk", advice="none | read | write")

    try:
        timeout_s = float(decl.get("timeout_s") or default_timeout_s)
    except (TypeError, ValueError) as exc:
        raise SkillToolError(tool_id, f"timeout_s is not a number: {exc!r}", field="timeout_s") from exc

    binding = SkillToolBinding(
        tool_id=tool_id, skill=skill_name, skill_dir=skill_dir, channel=channel, expects=expects,
        env_mode=env_mode, timeout_s=min(max(timeout_s, 0.5), 1800.0),
        dialect=(str(decl["dialect"]) if decl.get("dialect") else None),
        script_rel=script_rel or "", script_abs=script_abs, windows_script_abs=windows_abs,
        body=body, flags=flags_map)

    win = decl.get("handler_script_windows")
    if script_rel and win:
        windows_abs, _rel = _skill_relative(skill_dir, str(win), tool_id,
                                            field="handler_script_windows")
        binding.windows_script_abs = windows_abs
            # the primary display path stays the POSIX one; the windows sibling is a fact
            # about the binding, not a second name in the report

    manifest: dict[str, Any] = {
        "id": tool_id,
        "title": decl.get("title") or name or tool_id,
        "description": decl.get("description") or "",
        "capability": decl.get("capability") or tool_id,
        "group": decl.get("group") or f"skill.{skill_name}",
        "risk": risk,
        "destructive": False,
        "reversible": bool(decl.get("reversible", False)),
        "idempotent": bool(decl.get("idempotent", False)),
        "parallel_safe": bool(decl.get("parallel_safe", False)),
        "tags": _string_list(decl, "tags", tool_id),
        "anti_patterns": _string_list(decl, "anti_patterns", tool_id),
        "see_also": _string_list(decl, "see_also", tool_id),
        "examples": list(decl.get("examples") or []),
        "priority": int(decl.get("priority") or 50),
        "timeout_s": binding.timeout_s,
        "input_schema": schema,
        "advertised": bool(decl.get("advertised", True)),
        "hidden_reason": decl.get("hidden_reason") or (
            "" if decl.get("advertised", True) else
            "declared by a skill with advertised = false; `registry.describe {id}` shows it, and "
            "setting `advertised = true` in the skill's tool.toml (or listing the id under "
            "`tools.enable`) turns it on"),
        "requires": list(decl.get("requires") or []),
        "open_world": True,
    }
    manifest["description"] = _describe(manifest["description"], binding)
    return manifest, binding


def _describe(description: str, binding: SkillToolBinding) -> str:
    where = binding.script_rel or "the skill's inline handler_body"
    out = {"json": "stdout is parsed as JSON and returned under `result`",
           "lines": "stdout comes back as `lines`",
           "text": "stdout comes back under `stdout`"}[binding.expects]
    tail = (f"Runs `{where}` through the shell runner with the arguments bound by "
            f"`{binding.channel}`; a nonzero exit is the only failure signal and {out}.")
    return (description.rstrip() + "\n\n" + tail) if description else tail


def _skill_relative(skill_dir: str, raw: str, tool_id: str, *, field: str = "handler_script") -> tuple[str, str]:
    """Resolve a declared script path, insisting it stays inside the skill directory.

    Returns `(absolute, relative)`. `..` and absolute paths are refused: a skill that can name
    any file on disk is not a skill, and the message says so instead of failing later.
    """
    cleaned = str(raw).strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        raise SkillToolError(tool_id, f"{field} {raw!r} must be relative to the skill directory",
                             field=field, advice="e.g. \"scripts/wordcount.sh\"")
    norm = posixpath.normpath(cleaned)
    if norm == ".." or norm.startswith("../") or posixpath.isabs(norm):
        raise SkillToolError(tool_id, f"{field} {raw!r} escapes the skill directory", field=field,
                             advice="keep scripts under <skill>/scripts/")
    ext = os.path.splitext(norm)[1].lower()
    if ext not in ALLOWED_EXT:
        raise SkillToolError(tool_id, f"{field} {raw!r} has extension {ext or '(none)'!r}",
                             field=field, advice="one of " + ", ".join(sorted(ALLOWED_EXT)))
    abs_path = os.path.abspath(os.path.join(skill_dir, *norm.split("/")))
    root = os.path.abspath(skill_dir)
    if os.path.commonpath([abs_path, root]) != root:              # pragma: no cover - belt
        raise SkillToolError(tool_id, f"{field} {raw!r} resolves outside the skill directory",
                             field=field)
    if not os.path.isfile(abs_path):
        raise SkillToolError(
            tool_id, f"{field} points at {norm}, which is not a file in the skill",
            field=field, advice="write the script first (fs.write), then load the skill")
    return abs_path, norm
