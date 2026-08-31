"""The reference docs must not name tools, arguments or knobs that do not exist.

Written after two claims in `docs/` drifted from the code while this branch was being
authored (a `shell.quote` tool that had not been built, an `env_mode` value that was never
`include`, and an `fs.roots` key that lives at the top level). Skill prose already had this
rule in `test_skills.py`; extending it to the docs is what keeps
`docs/TOOL-CONTRACT.md`-style claims ("written from the code") an executed check instead of
an intention.

Scope on purpose: `PLAN.md` is excluded. It is the roadmap, and its whole job is naming
things that do not exist yet - checking it would only teach the author to stop being
specific.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC_FILES = sorted(
    [p for pat in ("docs/*.md", "docs/adr/*.md", "README.md", "skills/*/SKILL.md",
                   "skills/*/references/*.md") for p in REPO.glob(pat)]
)
assert DOC_FILES, "the docs the suite checks disappeared"

# `fs.read {path, offset:0}` / `engine.call(...)` idioms: an id and a brace list in ONE
# code span. Requiring both in the same span is what keeps prose like "returns
# `{count, matches}`" from being misread as a call signature.
CALL_RE = re.compile(r"`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)[ \t]*\{([^`]*)\}`")
# A bare dotted name in the namespaces that belong to us.
BARE_RE = re.compile(r"`((?:fs|shell|registry|skills|profile|toolkit)\.[a-z_][a-z0-9_]*)`")
CONFIG_RE = re.compile(r"`((?:fs|policy|budget|shell|skills|state|tools|mcp)\.[a-z_][a-z0-9_]*)`")

# `Engine.call` keyword arguments, which docs legitimately write next to tool arguments.
ENGINE_KWARGS = {"ctx", "dry_run", "max_output_bytes", "idempotency_key", "approval_token",
                 "args", "tool_id"}

# Named deliberately in the docs as future work; each must cite a phase so this list cannot
# quietly become a graveyard. (Checked by `test_pending_names_are_annotated`.)
PENDING = {
    "fs.redo": "P3",
    "policy.grant": "P3",
    "skills.install": "P2",
    "skills.allow_install": "P2",
}


FILE_EXT = {"json", "yaml", "yml", "toml", "md", "py", "sh", "ps1", "psm1", "txt", "ndjson",
            "lock", "tar", "cfg", "ini", "csv", "html", "xml"}


def _top_level_names(inner: str) -> list[str]:
    """Argument *names* at depth 0 of a `{a, b: 1, c: [d]}` fragment.

    Two rules that keep this from shouting at correct prose: a value after `:` is not a name
    (`{background: true}` documents `background`, not an argument called `true`), and a
    nested bracket group is skipped so `{edits: [{old_text, new_text}]}` only asserts
    `edits` exists at the top level.
    """
    out: list[str] = []
    depth, buf, skip = 0, "", False

    def flush(seg: str) -> None:
        nonlocal skip
        if skip:
            skip = False
            return
        tok = seg.strip().strip("[]?").strip()
        if re.fullmatch(r"[a-z][a-z0-9_]*", tok):
            out.append(tok)

    for ch in inner:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if depth == 0 and ch in ",{}:":
            flush(buf)
            buf = ""
            skip = ch == ":"
            continue
        if depth == 0:
            buf += ch
    flush(buf)
    return out


@pytest.fixture(scope="module")
def toolkit():
    from skeletonkey.core.config import Config
    from skeletonkey.toolkit import build

    cfg = Config.load(cwd=str(REPO), overrides={"log_level": "ERROR"})
    return build(config=cfg), cfg


def _docs() -> list[tuple[str, str]]:
    return [(str(p.relative_to(REPO)), p.read_text(encoding="utf-8")) for p in DOC_FILES]


def test_every_documented_call_shape_matches_a_real_schema(toolkit):
    tk, _ = toolkit
    tools = {m.id: m for m in tk.engine.registry.all()}
    problems: list[str] = []
    checked = 0
    for name, text in _docs():
        for m in CALL_RE.finditer(text):
            tool_id, inner = m.group(1), m.group(2)
            line = text[: m.start()].count("\n") + 1
            checked += 1
            man = tools.get(tool_id)
            if man is None:
                problems.append(f"{name}:{line}: documents `{tool_id} {{…}}` but {tool_id} is not registered")
                continue
            props = set((man.input_schema or {}).get("properties", {}))
            for arg in _top_level_names(inner):
                if arg in props or arg in ENGINE_KWARGS:
                    continue
                problems.append(f"{name}:{line}: {tool_id} has no argument {arg!r} "
                                f"(it accepts {sorted(props)})")
    assert checked >= 20, f"the doc corpus got thin: only {checked} call shapes found"
    assert not problems, "docs name arguments that do not exist:\n" + "\n".join(problems)


def test_every_named_tool_or_knob_resolves(toolkit):
    tk, cfg = toolkit
    tools = {m.id for m in tk.engine.registry.all()}
    capabilities = set(getattr(tk.engine.profile, "capabilities", set()) or set())
    config_paths: set[str] = set()
    for f in dataclasses.fields(cfg):
        config_paths.add(f.name)
        sub = getattr(cfg, f.name)
        if dataclasses.is_dataclass(sub) and not isinstance(sub, type):
            for sf in dataclasses.fields(sub):
                config_paths.add(f"{f.name}.{sf.name}")
    problems: list[str] = []
    for name, text in _docs():
        for m in BARE_RE.finditer(text):
            tok = m.group(1)
            line = text[: m.start()].count("\n") + 1
            if tok.rsplit(".", 1)[1] in FILE_EXT:
                continue  # `profile.json` is a file, not a tool id - the same trap
                          # `test_skills.py` documents for skill bodies
            if tok in tools or tok in capabilities or tok in config_paths:
                continue
            if tok in PENDING:
                continue
            problems.append(f"{name}:{line}: `{tok}` is neither a registered tool, a probed "
                            f"capability, nor a config key")
    assert not problems, "docs reference names that do not resolve:\n" + "\n".join(problems)


def test_config_keys_named_in_docs_exist(toolkit):
    tk, cfg = toolkit
    tools = {m.id for m in tk.engine.registry.all()}
    known: set[str] = set()
    for f in dataclasses.fields(cfg):
        known.add(f.name)
        sub = getattr(cfg, f.name)
        if dataclasses.is_dataclass(sub) and not isinstance(sub, type):
            for sf in dataclasses.fields(sub):
                known.add(f"{f.name}.{sf.name}")
    problems = []
    for name, text in _docs():
        for m in CONFIG_RE.finditer(text):
            tok = m.group(1)
            line = text[: m.start()].count("\n") + 1
            if tok in tools or tok in PENDING:
                continue  # `fs.patch` is a tool in the `fs` namespace; `fs.redo` is a cited
                          # roadmap name - both belong to the other check, not this one
            if tok not in known:
                problems.append(f"{name}:{line}: `{tok}` is not a config key "
                                f"(top-level keys include: roots, workspace, state.dir)")
    assert not problems, "docs name config knobs that the loader does not read:\n" + "\n".join(problems)


def test_pending_names_are_cited_in_the_line_that_uses_them():
    """`PENDING` is the only escape hatch, so every use of it must cite its phase inline.

    A name in `PENDING` that no doc mentions is a failure too: the list is a register of
    roadmap claims, not a place to park a claim that turned out to be wrong.
    """
    seen: set[str] = set()
    problems: list[str] = []
    for name, text in _docs():
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            for tok, cite in PENDING.items():
                if f"`{tok}" not in line:
                    continue
                seen.add(tok)
                # a 3-line window centred on the mention: prose wraps, and the citation may
                # sit either side ("When P2 adds X, its `x.flag` defaults to false"). The
                # point is local context, not a whole-file keyword hunt.
                window = "\n".join(lines[max(0, idx - 1):idx + 2])
                if cite not in window:
                    problems.append(f"{name}:{idx + 1}: `{tok}` is a roadmap name, but the "
                                    f"surrounding lines never cite {cite!r}")
    unused = sorted(set(PENDING) - seen)
    assert not unused, f"PENDING entries nothing refers to - delete them: {unused}"
    assert not problems, "uncited roadmap names in docs:\n" + "\n".join(problems)


def test_error_codes_in_doc_tables_are_real():
    """Every ``| `CODE` |`` row in a docs table must be a code the engine can emit.

    Table cells only: a bare uppercase token in prose is as likely to be `BOM` or `UAC`, and
    a test that shouts at prose gets deleted in the next tidy-up.
    """
    from skeletonkey.core.errors import E

    known: set[str] = set()
    for name in dir(E):
        if not name.isupper():
            continue
        val = getattr(E, name)
        known.add(name)
        code = getattr(val, "code", None)
        if isinstance(code, str):
            known.add(code.upper())

    row = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]{2,})`\s*\|", re.M)
    problems = []
    for name, text in _docs():
        for m in row.finditer(text):
            tok = m.group(1)
            if tok in known:
                continue
            line = text[: m.start()].count("\n") + 1
            problems.append(f"{name}:{line}: `{tok}` is documented as an error code but is not in E")
    assert not problems, "unknown error codes in docs:\n" + "\n".join(problems)
