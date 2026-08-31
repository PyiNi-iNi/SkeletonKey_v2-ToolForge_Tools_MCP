# Skill spec

A **skill** is procedural knowledge an agent loads on demand, plus — optionally — tool
declarations. It follows the Agent Skills shape (`SKILL.md` with YAML frontmatter and
`references/` read only when needed) and adds the one thing a generic skill pack lacks:
a compiled path from *instructions* to *callable tools*.

```
skills/
  <skill-name>/
    SKILL.md          # frontmatter + the body an agent gets injected
    references/*.md   # long-form detail, read on demand (never auto-injected)
    scripts/*.{sh,ps1,py,psm1}
    tool.toml         # optional: [[tool]] declarations -> registry entries
```

Discovery is `SkillLoader.discover()`: every subdirectory of every `skills.dirs`
(default `["skills"]`, relative to the config's workspace) **that contains a
`SKILL.md`**. A directory without one is not an error, it is not a skill. A directory
whose name starts with `_` is loaded and marked `disabled` (a way to park a skill
without deleting it).

## Frontmatter

The parser (`skills/loader.py::_parse_frontmatter`) is a deliberate YAML subset, because
hand-written headers must not need a YAML dependency or a parser that rejects prose:

| form | example |
| --- | --- |
| `key: value` | `name: fs-safe-refactor` |
| quoted scalar with a colon | `description: "when: in doubt"` |
| inline list | `tags: [refactor, rename, fs]` |
| block list | `- rename` under `triggers:` |
| nested map | `meta:\n  owner: dime` |
| folded / literal block scalars | `when_to_use: >-` … `notes: \|` |
| comments | `# not data` |

Unknown keys are ignored, never fatal: a skill written for a future version must still
load today. Machine-checkable subset: `schemas/skill-frontmatter.schema.json`.

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | directory name | kebab-case. A frontmatter `name` wins over the directory; on collision the **first** directory wins and the loser gets `shadowed by earlier …` in `parse_notes` instead of replacing it |
| `description` | first heading (or `""`) | one line, what the matcher shows and what a model reads first |
| `when_to_use` | `""` | the trigger sentence, in the agent's vocabulary. Doubles as the trigger list when `triggers` is absent |
| `triggers` | — | explicit list; only read when `when_to_use` is empty |
| `tags` | `[]` | extra match vocabulary + shown in `skills.list` |
| `requires` | `[]` | capability names this skill assumes; surfaced by `skills.list`, not enforced in P1 (P2 gates on them) |
| `disable_for` | `[]` | disable this skill when the profile *has* one of these capabilities |
| `priority` | `50` | tiebreak inside the score, honoured only if `skills.respect_priority` |
| `version` | `1` | rendered as `# skill: name (vN)` when ≠ `1` |
| `license` | `""` | carried through for distribution (P6) |
| `allowed-tools` | `[]` | the tool ids this skill's guidance is written against (hyphen in the header, underscore on the object) |

Body size is capped by `skills.max_body_bytes` (32 000): a longer body is cut and
`body truncated at N bytes` lands in `parse_notes` — never silently.

## Matching and injection

`match(task, limit=3, max_tokens=None)` is lexical and explainable on purpose (an
autopilot must be able to answer "why was this injected?" without a model call):

```
toks = {w for w in task.lower() if len(w) >= 3}
hay  = tokens(name + when_to_use + description + triggers + tags)
score = |toks ∩ hay| / max(4.0, √|hay|) + (0.1 * priority/100 if respect_priority else 0)
```

Sorted by `(-score, name)`; skills are taken while `Σ token_estimate ≤ max_tokens`
(the **first** match is taken even if it is over budget — an empty block is worse than a
big one), capped at `limit`. `max_tokens` defaults to `skills.max_inline_tokens × limit`.

`skills.load {name}` on an unknown name is an `ENOENT` tool error carrying
`details.did_you_mean` (near-miss names) and advice to run `skills.list` — a typo should
not be a dead end.

`context_block(task)` returns the thing to paste into a prompt:

```python
{"block": "# skill: fs-safe-refactor\n\n…", "skills": [ {…}, … ],
 "tokens": 1180, "budget": 3600, "unused_budget": 2420}
```

Those five keys are present **whether or not anything matched**, so a caller never has to
guard `unused_budget`. Per-skill rendering budget is
`min(max_inline_tokens, budget // n_picked)`, and overflow is cut at the last paragraph
boundary followed by:

```
...[truncated: read the file for the rest]
```

(The three dots there are the marker itself, not this document abbreviating — it is a
verbatim copy of `loader.py`'s truncation string, because agents key on it.)

That marker is the whole point of progressive disclosure step 2: `skills.load {name,
references: ["references/eol-and-encoding.md"]}` re-renders the same block with the named
reference files appended (a missing file becomes `[missing]`, not a stack trace).
`references/` is limited to `.md`/`.txt`; `scripts/` to `.sh`, `.ps1`, `.py`, `.psm1`.

## `tool.toml` — declarative tools

`[[tool]]` tables (or a single `[tool]` table, which is friendlier for a
one-tool skill) become real `ToolManifest`s at build time, `group = "skill.<name>"` and
`source = "skill:<name>"` unless the manifest overrides them, with `handler_script`,
`handler_script_windows` and `expects` recorded for the P2 compiler.

In P1 they are registered but **not advertised** (`advertised = false`, plus a
`hidden_reason` naming the phase that wires them) and calling one raises a *tool* error,
not a crash:

```json
{"ok": false, "error": {"code": "NOT_IMPLEMENTED",
  "message": "skill-declared tool 'shell.selftest' has no executor yet",
  "hint": "…", "details": {"phase": "2 (skill runtime + tool compiler)", "plan": "PLAN.md"}},
 "next_actions": [{"tool": "registry.search", "args": {"query": "shell.selftest"}}]}
```

Why register them at all? So `registry.search`/`describe` already know the contract, so
the token budget accounts for them the day they turn on, and so an agent that finds the
name learns the exact reason it cannot run yet — instead of inventing a shell command to
do the same thing. An unreadable `tool.toml` lands in that skill's `parse_notes` (`"tool.toml is
unreadable: …"`); a manifest that cannot be built lands in
`registry.load_errors[{skill_tool, stage: "declare", error}]`. Either way the rest of the
skill still loads — one bad file never hides a good skill, and never silently drops a
tool.

P2 replaces the stub with a compiler: `handler_script` + `input_schema` →
`shell.run {dialect, script|argv, expects}`. The three bindings that will be
supported are exactly `--flag {name}`, `$ARG_json`, and stdin-JSON (PowerShell); anything
else must stay a documented manual step, not an emergent `eval`. The argv-vs-interpolation
rule those bodies compile against is [ADR 0007](adr/0007-argv-over-interpolation.md). Note what P1 does *not*
have: no install path at all (`skills.dirs` is operator input, and there is no
`skills.install`), so a skill can only arrive by commit. When P2 adds installation, its
`skills.allow_install` flag must default to `false` — a skill that can run arbitrary
commands should be a decision, not a default (see `docs/SECURITY-MODEL.md` §Gaps).

## Working examples in this repo

| Skill | Declares | Notes |
| --- | --- | --- |
| `fs-safe-refactor` | — | multi-file rename/patch discipline; `references/eol-and-encoding.md` and `references/undo-and-journal.md` carry the detail so the body stays small |
| `shell-crossplatform` | `shell.selftest`, `shell.quote_check` | both `advertised = false` stubs with `handler_script{,_windows}`; the body's claims are pinned to the real sentinel/dialect behaviour |

`tests/test_skills.py` checks the shipped packs, not just the loader: a description,
a `when_to_use`, `allowed-tools` populated, a body under 4 000 tokens that opens with a
heading, and no dangling tool name in the prose (dotted tokens are matched against the
registry, with a file-extension post-filter so `profile.json` is not read as a tool id —
a `(?!\.\w)` lookahead was tried first and it also hid real ids, so it is not the rule).
Declaration-level tests cover the rest: `source == "skill:<name>"`, `group ==
"skill.<name>"`, and a `input_schema = """…"""` TOML string that must parse into a
schema with its `required` list intact.

## Authoring checklist

1. Write the body as *decisions in order*, not a tutorial: what to check, what to call,
   what to do when it fails. Agents follow numbered imperatives; they skim prose.
2. Put anything longer than ~40 lines into `references/` and name the file in the body.
3. Keep `when_to_use` in the agent's words ("renaming a symbol across the repo"), because
   that string *is* the matcher.
4. Do not name a tool you have not verified exists. The test suite will find it.
5. Every `[[tool]]` you declare must have a `capability` (so gating works) and an
   `input_schema` with `additionalProperties: false`.
6. `sk skills match "<task you had in mind>"` must return your skill before you commit it.
7. `python -m skeletonkey.cli skills load <name>` and read the rendered block — that is
   all the model will see unless it asks for more.
