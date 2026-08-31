"""Skill loading: SKILL.md directories -> instruction packs (and, Phase 2, tools).

A *skill* is procedural knowledge (how to do X well here); a *tool* is a
capability (the ability to do X). The distinction is deliberate: skills get
injected into context and can be matched by trigger predicates, tools get
advertised and executed. Skills may also declare `tool:` blocks, which the
registry ingests as real ToolManifests - that is the "dynamic toolset" tail:
drop a directory in and the agent's capability set changes.

Format (docs/SKILLS-SPEC.md):
    skills/<name>/SKILL.md          frontmatter + body
    skills/<name>/references/*.md   read on demand only
    skills/<name>/scripts/*         executable helpers (invoked via shell.run)
    skills/<name>/tool.toml         optional tool manifests
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import E, SkeletonKeyError
from ..core.util import estimate_tokens

FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$", re.M)


@dataclass
class Skill:
    name: str
    path: str
    description: str = ""
    when_to_use: str = ""
    version: str = "1"
    triggers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    priority: int = 50
    token_estimate: int = 0
    body: str = ""
    references: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    license: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    disabled: bool = False
    parse_notes: list[str] = field(default_factory=list)

    def to_dict(self, *, with_body: bool = False) -> dict[str, Any]:
        out = {"name": self.name, "description": self.description, "version": self.version,
               "when_to_use": self.when_to_use, "triggers": self.triggers, "tags": self.tags,
               "token_estimate": self.token_estimate, "references": self.references,
               "scripts": self.scripts, "path": self.path}
        if self.requires:
            out["requires"] = self.requires
        if self.allowed_tools:
            out["allowed_tools"] = self.allowed_tools
        if self.disabled:
            out["disabled"] = True
        if self.parse_notes:
            out["notes"] = self.parse_notes
        if with_body:
            out["body"] = self.body
        return out

    def render_injection(self, *, max_tokens: int = 1200, with_references: list[str] | None = None) -> str:
        """Progressive disclosure: body first, references only if asked for."""
        head = f"# skill: {self.name}" + (f" (v{self.version})" if self.version != "1" else "")
        budget = max_tokens * 4
        body = self.body
        if with_references:
            chunks = [body]
            for ref in with_references:
                p = os.path.join(self.path, ref)
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        chunks.append(f"\n\n## reference: {ref}\n" + fh.read())
                except OSError:
                    chunks.append(f"\n\n## reference: {ref}\n[missing]")
            body = "".join(chunks)
        if len(body) > budget:
            cut = body[:budget]
            nl = cut.rfind("\n\n")
            body = (cut[:nl] if nl > budget // 2 else cut) + "\n\n...[truncated: read the file for the rest]"
        return f"{head}\n\n{body.strip()}\n"


class SkillLoader:
    def __init__(self, dirs: list[str], *, max_body_bytes: int = 32_000,
                 profile: Any = None, respect_priority: bool = True,
                 max_inline_tokens: int = 1200) -> None:
        self.dirs = [os.path.abspath(d) for d in dirs]
        self.max_body_bytes = max_body_bytes
        self.profile = profile
        # `build()` passes these from SkillConfig; a documented setting that changes
        # nothing is indistinguishable from a bug, so they are wired, not decorative.
        self.respect_priority = respect_priority
        self.max_inline_tokens = int(max_inline_tokens)
        self._cache: dict[str, Skill] = {}
        self.errors: list[dict[str, str]] = []

    def discover(self, *, refresh: bool = False) -> list[Skill]:
        if self._cache and not refresh:
            return list(self._cache.values())
        found: dict[str, Skill] = {}
        self.errors = []
        for d in self.dirs:
            if not os.path.isdir(d):
                continue
            for entry in sorted(os.listdir(d)):
                sdir = os.path.join(d, entry)
                skill_md = os.path.join(sdir, "SKILL.md")
                if not os.path.isdir(sdir) or not os.path.isfile(skill_md):
                    continue
                try:
                    skill = self._parse(entry, sdir, skill_md)
                except Exception as exc:
                    self.errors.append({"path": skill_md, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if skill.name in found:
                    found[skill.name].parse_notes.append(
                        f"shadowed by earlier {found[skill.name].path}")
                    continue
                found[skill.name] = skill
        self._cache = found
        return list(found.values())

    def _parse(self, name: str, sdir: str, skill_md: str) -> Skill:
        with open(skill_md, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read(self.max_body_bytes + 4096)
        notes: list[str] = []
        meta: dict[str, Any] = {}
        body = text
        m = FM_RE.match(text)
        if m:
            meta = _parse_frontmatter(m.group(1))
            body = text[m.end():]
        else:
            notes.append("no YAML frontmatter; using directory name and first heading")
            first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            meta = {"description": first.lstrip("# ").strip()}
        if len(text) > self.max_body_bytes:
            notes.append(f"body truncated at {self.max_body_bytes} bytes")
            body = body[: self.max_body_bytes]
        triggers = _as_list(meta.get("when_to_use") or meta.get("triggers"))
        refs = _list_files(os.path.join(sdir, "references"), (".md", ".txt"))
        scripts = _list_files(os.path.join(sdir, "scripts"), (".sh", ".ps1", ".py", ".psm1"))
        tools: list[dict[str, Any]] = []
        tool_path = os.path.join(sdir, "tool.toml")
        if os.path.isfile(tool_path):
            tools = _read_tool_toml(tool_path, notes)
        desc = str(meta.get("description", "")).strip()
        requires = _as_list(meta.get("requires"))
        disallowed = _as_list(meta.get("disable_for"))
        disabled = os.path.basename(sdir).startswith("_") or (
            bool(self.profile) and any(r in disallowed for r in getattr(self.profile, "capabilities", set()))
        )
        skill = Skill(
            name=str(meta.get("name") or name), path=sdir, description=desc,
            when_to_use=str(meta.get("when_to_use", "")).strip(),
            version=str(meta.get("version", "1")), triggers=triggers,
            tags=_as_list(meta.get("tags")), requires=requires,
            priority=int(meta.get("priority", 50) or 50), body=body.strip(), references=refs,
            scripts=scripts, tools=tools, license=str(meta.get("license", "")),
            allowed_tools=_as_list(meta.get("allowed-tools")), disabled=disabled,
            parse_notes=notes,
        )
        skill.token_estimate = estimate_tokens(body) + estimate_tokens(skill.when_to_use)
        return skill

    def get(self, name: str) -> Skill:
        self.discover()
        try:
            return self._cache[name]
        except KeyError:
            near = sorted(set(self._cache) - {name})
            guess = [n for n in near if name.lower() in n.lower()][:3]
            raise SkeletonKeyError(
                E.ENOENT, f"no skill named {name!r}",
                details={"known": near, "did_you_mean": guess, "dirs": self.dirs,
                         "advice": "skills.list shows every discovered skill"},
            ) from None

    def match(self, task: str, *, limit: int = 3, max_tokens: int | None = None) -> list[Skill]:
        """Pick skills whose trigger vocabulary overlaps the task text.

        Deliberately lexical + explainable: an autopilot must be able to answer
        "why was this injected?" without a model call.

        The budget defaults to `skills.max_inline_tokens` per matched skill, so the
        config knob is what actually caps prompt growth.
        """
        if max_tokens is None:
            max_tokens = self.max_inline_tokens * max(1, limit)
        toks = set(re.findall(r"[a-z0-9]{3,}", (task or "").lower()))
        scored: list[tuple[float, Skill]] = []
        for skill in self.discover():
            if skill.disabled:
                continue
            hay = set(re.findall(r"[a-z0-9]{3,}",
                                 " ".join([skill.name, skill.when_to_use, skill.description,
                                          " ".join(skill.triggers), " ".join(skill.tags)]).lower()))
            overlap = toks & hay
            if not overlap:
                continue
            prio = 0.1 * (skill.priority / 100) if self.respect_priority else 0.0
            score = len(overlap) / max(4.0, (len(hay) or 1) ** 0.5) + prio
            scored.append((score, skill))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        picked: list[Skill] = []
        spent = 0
        for _score, skill in scored:
            if spent + skill.token_estimate > max_tokens and picked:
                break
            picked.append(skill)
            spent += skill.token_estimate
            if len(picked) >= limit:
                break
        return picked

    def context_block(self, task: str, *, limit: int = 3,
                      max_tokens: int | None = None) -> dict[str, Any]:
        """What the autopilot injects into the prompt for this task."""
        if max_tokens is None:
            max_tokens = self.max_inline_tokens * max(1, limit)
        picked = self.match(task, limit=limit, max_tokens=max_tokens)
        if not picked:
            # Same keys either way: a caller that branches on `unused_budget` being
            # present is a caller that crashes on the empty case.
            return {"block": "", "skills": [], "tokens": 0, "budget": max_tokens,
                    "unused_budget": max_tokens}
        # Per skill: the smaller of the caller's share and the configured per-skill cap,
        # so `max_inline_tokens` means what its name says even when one skill matched.
        per = min(self.max_inline_tokens, max_tokens // max(1, len(picked)))
        block = "\n\n".join(s.render_injection(max_tokens=per) for s in picked)
        return {"block": block, "skills": [s.to_dict() for s in picked],
                "tokens": estimate_tokens(block),
                "budget": max_tokens, "unused_budget": max(0, max_tokens - estimate_tokens(block))}

    def manifest_candidates(self) -> list[dict[str, Any]]:
        out = []
        for skill in self.discover():
            for tool in skill.tools:
                out.append({**tool, "group": tool.get("group") or f"skill.{skill.name}",
                            "source": f"skill:{skill.name}"})
        return out


# -------------------------------------------------------------------- helpers


_BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*$")


def _parse_frontmatter(fm: str) -> dict[str, Any]:
    """Tiny YAML subset, enough for a hand-written SKILL.md header.

    Supports `key: value`, inline lists `[a, b]`, block lists of `- item`, nested
    maps, and - the one that matters for prose - folded/literal block scalars
    (`description: >-`, `when_to_use: |`). Authors write those constantly because
    it is how you keep a long description readable, and dropping them silently turns
    a skill into an invisible one.
    """
    meta: dict[str, Any] = {}
    lines = fm.split("\n")
    i = 0

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" \t"))

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if raw.startswith((" ", "\t")):
            # an indented block we did not claim: belongs to the previous key
            i += 1
            continue
        m = KEY_RE.match(raw)
        if not m:
            i += 1
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if val and _BLOCK_SCALAR.match(val):
            text, i = _read_block(lines, i + 1, folded=val.startswith(">"),
                                  header=val)
            meta[key] = text
            continue
        if val:
            meta[key] = _scalar(val)
            i += 1
            continue
        # `key:` with no value: a list, a nested map, or an empty scalar
        items: list[Any] = []
        mapping: dict[str, Any] = {}
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                j += 1
                continue
            if indent_of(nxt) == 0:
                break
            item = nxt.strip()
            if item.startswith("- "):
                items.append(_scalar(item[2:]))
            else:
                k2, sep, v2 = item.partition(":")
                if sep:
                    mapping[k2.strip().lower()] = _scalar(v2)
                else:
                    items.append(_scalar(item))
            j += 1
        if items and mapping:
            items = [*mapping.values(), *items]
            mapping = {}
        meta[key] = items if items else (mapping or "")
        i = j
    return meta


def _read_block(lines: list[str], start: int, *, folded: bool, header: str) -> tuple[str, int]:
    """Collect an indented `|`/`>` block. `|` keeps line breaks, `>` folds them."""
    collected: list[str] = []
    block_indent: int | None = None
    explicit = re.fullmatch(r"[|>][+-]?(\d+)?", header)
    wanted = int(explicit.group(1)) if explicit and explicit.group(1) else 0
    j = start
    while j < len(lines):
        line = lines[j]
        if line.strip() and (len(line) - len(line.lstrip(" \t"))) == 0:
            break  # a new top-level key ends the block
        if not line.strip():
            collected.append("")
            j += 1
            continue
        ind = len(line) - len(line.lstrip(" \t"))
        if block_indent is None:
            block_indent = ind if not wanted else max(ind, wanted)
        if ind < (block_indent or 0):
            break
        collected.append(line[block_indent or 0:])
        j += 1
    while collected and not collected[-1].strip():
        collected.pop()
    if folded:
        out: list[str] = []
        for line in collected:
            if not line:
                out.append("\n")
            elif out and not out[-1].endswith("\n"):
                out[-1] = out[-1].rstrip() + " " + line
            else:
                out.append(line)
        text = "".join(x if x == "\n" else x + "\n" for x in out)
    else:
        text = "\n".join(collected) + "\n"
    if header.endswith("-"):
        text = text.rstrip("\n")
    elif not header.endswith("+"):
        text = text.rstrip("\n") + ("\n" if folded else "")
    return text.strip() if folded else text, j


def _scalar(text: str) -> Any:
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith("[") and t.endswith("]"):
        return [x.strip().strip("'\"") for x in t[1:-1].split(",") if x.strip()]
    if t[0] in "\"'" and t[-1] == t[0] and len(t) > 1:
        return t[1:-1]
    low = t.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    return t


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, dict):
        return [str(v) for v in value.values()]
    return [x.strip() for x in re.split(r"[,;]", str(value)) if x.strip()]


def _list_files(dirpath: str, exts: tuple[str, ...]) -> list[str]:
    if not os.path.isdir(dirpath):
        return []
    out = []
    for name in sorted(os.listdir(dirpath)):
        p = os.path.join(dirpath, name)
        if os.path.isfile(p) and name.lower().endswith(exts):
            out.append(os.path.relpath(p, os.path.dirname(dirpath)).replace(os.sep, "/"))
    return out


def _read_tool_toml(path: str, notes: list[str]) -> list[dict[str, Any]]:
    try:
        import tomllib

        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except ImportError:
        notes.append("tool.toml present but tomllib unavailable (needs py3.11+) - ignored")
        return []
    except (OSError, ValueError) as exc:
        notes.append(f"tool.toml unreadable: {exc}")
        return []
    # `[[tool]]` yields a list, `[tool]` a dict, `tools = [...]` a list of inline
    # tables; all three show up in real files, so accept all three shapes.
    raw = data.get("tools")
    if raw is None:
        raw = data.get("tool")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        notes.append("tool.toml has no [[tool]] or tools array; ignored")
        return []
    out = [t for t in raw if isinstance(t, dict)]
    if len(out) != len(raw):
        notes.append(f"tool.toml: {len(raw) - len(out)} non-table entries ignored")
    return out
