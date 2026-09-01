# Write a skill

A skill is procedural knowledge the agent loads on demand — plus, optionally,
tools. The spec is [SKILLS-SPEC](../SKILLS-SPEC.md); this page is the 5-minute
walkthrough with a real example from this repo.

## 1. A skill is a directory with `SKILL.md`

```
skills/
  my-skill/
    SKILL.md
    references/
      notes.md        # read only when the agent needs it
    tool.toml         # optional declarative tools
```

Discovery: every subdirectory of every `skills.dirs` entry (default `skills/`,
relative to the workspace) **that contains a `SKILL.md`** is a skill. A
directory without one is not an error — it is not a skill. A name starting
with `_` loads disabled (park it, don't delete it).

## 2. Frontmatter — the machine subset

`docs/SKILLS-SPEC.md` defines the YAML subset the parser accepts. The four
fields that matter most:

| Field | Meaning |
| --- | --- |
| `name` | stable id, `[a-z][a-z0-9-]*` |
| `description` | when to use it — this is what the matcher reads |
| `when_to_use` | the trigger vocabulary, written for retrieval, not humans |
| `allowed-tools` | the namespaces the skill is allowed to call; `requires` |

Unknown keys are ignored, never fatal: a skill written for a future version
still loads today. The machine-checkable schema is at
`schemas/skill-frontmatter.schema.json`.

## 3. Body — the discipline, not the prose

The body is the contract the agent follows. This repo's skills are short and
imperative (`skills/fs-safe-refactor/SKILL.md` is the model): locate → read →
propose → apply → verify. Every claim you write is linted —
`tests/test_skills.py` checks that every `fs.*` id in a skill body is a real
tool and every `{arg}` a real schema key.

## 4. Declarative tools — `tool.toml`

A `[[tool]]` section adds a registry entry with the same manifest fields as
builtin tools. Keys are validated (see the spec's key table); a tool that
fails **at load time is a load error** — it never half-registers:

```toml
[[tool]]
id = "my.transform"
title = "Transform a text file"
description = "Apply a regex table to a file; writes a sibling .out"
handler_script = "transform.py"   # relative to the skill dir; inlined, never cd'd to
args_via = "argv_json"
expects = "json"
env_mode = "clean"
risk = "write"
reversible = true
```

Declarative tools run through the same engine, ledger, budget and policy as
every other tool, so a `tool.toml` tool gets receipts, sandboxing
(`destructive = true` is refused outright) and a `NONZERO_EXIT` with the tail
instead of a corrupted process — for free.

## 5. Install and verify

```bash
sk skills list                  # does it load? load errors are listed, never silent
sk skills match "fix the flaky windows path test"   # what the agent will see
sk skills load my-skill         # the rendered context block + injections
sk doctor                       # the "skills" section: pack count + errors
```

`sk doctor` is the fast loop: it reports parse notes, tool-compile errors and
the pack count without you reading any source.

## 6. Hot reload

`sk live demo` (a `live.start` session) reloads an edited *program* file on
save; skill packs are discovered at build time, so a pack edit needs a
restart. See [LIVE-HMR.md](../LIVE-HMR.md). Keep the change and its
load-error check in one commit either way.

## Authoring checklist

- [ ] `name` matches `[a-z][a-z0-9-]*`
- [ ] `description` says *when*, not *what*
- [ ] every tool id in the body exists (the lint proves it)
- [ ] `tool.toml` tools declare `risk`, a binding (`args_via` + `handler_script`) and `input_schema`
- [ ] `sk skills list` shows it, its "doctor" skills section is error-free
- [ ] a wire test exercises the tool end-to-end (contract: one per tool)
