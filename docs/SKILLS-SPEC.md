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
| `requires` | `[]` | capability names this skill assumes; surfaced by `skills.list`. A `[[tool]]`'s own `requires` go through the same advertisement gate a built-in uses, so a tool whose binary is absent is registered, gated, and explained rather than advertised and failing |
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

A `[[tool]]` table (or a single `[tool]`, which is friendlier for a one-tool skill) is
compiled by `skeletonkey/skills/compiler.py` into a real `ToolManifest` plus a handler
that runs **one script**. `group = "skill.<name>"` and `source = "skill:<name>"` unless
the declaration overrides them; the id defaults to `skill.<skill>.<name>` (dashes in the
tool name are kept, underscores in the *skill* name are not touched).

A skill-authored tool is a manifest plus a subprocess, never executed Python: the
sandbox, the budget, the ledger and the error taxonomy apply unchanged, and a broken
script is a `NONZERO_EXIT` with the tail attached instead of a corrupted process.

### Keys a declaration may use

| key | default | meaning |
| --- | --- | --- |
| `id` | `skill.<skill>.<name>` | must match `^[a-z][a-z0-9_.-]*$`; may equal a built-in's id **only** with `tools.override_builtin = true` |
| `name` | — | used for the default id when `id` is absent |
| `title`, `description`, `capability`, `group`, `tags`, `see_also`, `anti_patterns`, `examples`, `priority` | — | carried into the manifest; `description` gets one appended sentence naming the script and its binding, so a host reading `tools/list` can tell a skill tool from a built-in |
| `handler_script` | — | path **relative to the skill directory**, extension in `.sh .bash .ps1 .psm1 .py`; the file's text is inlined into the payload (see below) |
| `handler_script_windows` | — | the PowerShell sibling, chosen when the dialect family is `powershell` |
| `handler_body` | — | inline script text; mutually exclusive with `handler_script` |
| `args_via` | `flags` | `flags` \| `argv_json` \| `stdin_json` \| `none` — the whole binding surface |
| `expects` | `text` | `json` \| `lines` \| `text`, same contract as `shell.run` |
| `env_mode` | `clean` | `clean` \| `inherit` \| `login`; `clean` keeps the bootstrap keys (PATH and friends) plus whatever the call passes, and drops your environment |
| `timeout_s` | 120 | clamped to 0.5–1800 |
| `dialect` | profile's | pins the interpreter (use it when the handler is written in one language) |
| `risk` | `write` | `none` \| `read` \| `write` only — and `destructive = true` is refused outright |
| `idempotent`, `parallel_safe`, `reversible`, `advertised`, `requires`, `flags`, `input_schema` | — | `requires` feeds the same advertisement gate as a built-in's; `flags` maps a property to a custom `--long-flag`; `input_schema` may be a table or a JSON string |

`anti_patterns`, `tags` and `see_also` are **lists of strings** (they are joined into the
advertised description); a table there is refused at compile time rather than crashing
`tools/list` later.

### The three bindings

```toml
args_via = "flags"       # path = "a.txt", re = true  ->  ["--path", "a.txt", "--re"]
args_via = "argv_json"   # whole object, one element   ->  [$ARG_json / "$1" / $args[0]]
args_via = "stdin_json"  # whole object on stdin, nothing in argv
args_via = "none"        # the script takes no input
```

`flags` is the default and the only one that needs the script to cooperate per property:
booleans become the bare flag, arrays repeat the flag, everything else is
`str()`/`json.dumps`. `argv_json` is what a body written in one piece wants: the compiler
substitutes `$ARG_json` with the dialect's own positional reference (`"$1"`, `$args[0]`,
`sys.argv[1]`) — the *marker*, never the value.

What is refused, with advice that says what to do instead:

* a `{placeholder}` naming a declared property in a `handler_body` — that is
  [ADR 0007](adr/0007-argv-over-interpolation.md)'s `eval` by another name (`{x}` that is
  *not* a property is that language's own syntax and passes);
* `$ARG_json` without `args_via = "argv_json"`, and `argv_json` with a body that never
  reads it (the args would vanish);
* `args_via = "none"` together with any property (a silently ignored argument is worse
  than an error);
* a `handler_script` that is absolute, escapes the skill directory, has a foreign
  extension, or does not exist;
* `destructive = true`, or a `risk` above `write`;
* a pinned `dialect` **and** a `dialect` property — the arg would override the pinned
  interpreter, so the property has to be renamed (`target_dialect` in
  `shell.quote_check`, which is the lesson that skill taught itself);
* a `dialect` property at all is consumed by the compiler — it selects the interpreter and
  never reaches the script.

Scripts are **inlined**, not invoked by path: `skills.dirs` usually sits outside the
sandbox roots, so `cd`-ing there to execute a file would either be refused or quietly
widen the roots. Inlining keeps the payload in the runner's temp dir; `keep_script` then
leaves the exact text on disk if a call needs attaching to a report.

### What a call returns

The envelope is `shell.run`'s, with provenance added, because `run_script` is the shared
executor (a skill tool does *not* call `engine.call("shell.run")` — that would ask for
approval twice for one action and write two ledger rows):

```json
{"ok": true, "data": {
   "exit_code": 0, "completed": true, "stdout": "{\"words\":6}\n", "stderr_tail": "",
   "result": {"words": 6}, "argv": ["--path", "notes.txt"],
   "args_via": "flags", "owner": "skill:wordcount",
   "skill": "wordcount", "skill_tool": "skill.wordcount.wordcount",
   "script": "scripts/wordcount.sh", "dialect": "bash", "duration_ms": 4}}
```

`result` holds the parsed payload (`lines` for `expects = "lines"`, raw stdout for text),
`argv` is echoed so a failure is reproducible by pasting it back, and `owner`/`skill_tool`
say who ran it. A skill tool can opt into previews by declaring a `dry_run` property —
that declaration is the author's promise that the script honours it, and the engine
stops second-guessing the tool either way.

### Failure at load time is a load error

A declaration that cannot compile never becomes a callable-but-broken tool:

```python
{"skill_tool": "skill.broken.gone", "stage": "compile", "path": "/abs/skill/dir",
 "error": "handler_script points at scripts/missing.sh, which is not a file in the skill",
 "advice": "write the script first (fs.write), then load the skill", "field": "handler_script"}
```

Those rows land in the registry's load-error list *and* in what `skills.list` reports under
`errors` (the tool takes only `refresh`); the same reply lists the compiled ids under
`skill_tools`, so "the skill loaded but offers nothing" has one place to look.
A declaration with neither script nor body is different in kind: it stays registered,
unadvertised-by-whatever-gate-applies, and reports `NOT_IMPLEMENTED` with `details.phase`,
so the id is searchable and the gap is named.

### Installing and removing packs

`skills.install {dir, name?, dry_run?}` copies a reviewed directory into a skills root and
syncs the registry **in this process**; `skills.uninstall {name, remove_files?, dry_run?}`
unregisters its tools and journals the deletion. Both are gated:

* `skills.allow_install = false` (the default) → `DENY_RULE` with the exact setting to
  flip, and `skills.install` is not even advertised; `dry_run` still answers, so the plan
  can be reviewed without holding the privilege.
* uninstall is deliberately *not* gated behind `allow_install` — removing capability is
  not escalating it — but it refuses with `CONFLICT` while a job from that skill is
  running, listing the `job_id`s, because the running job's script lives in the directory
  being deleted.
* the copy goes through `fs.write`/`fs.delete`, so it is journalled (`undo` comes back in
  the payload) and constrained by `roots`/`deny`. Reading the source needs no privilege,
  writing outside the roots does.
* only `.md .toml .txt .sh .bash .ps1 .psm1 .py` are copied; symlinks, files over 512 KB,
  and packs over 24 files or 2 MB in total are skipped with a warning, and `git_ref` is
  `NOT_IMPLEMENTED` until P6's signing/review story exists.

`git_ref` refusal is a *tool error with a next_action*, not a stub: clone it yourself,
read the diff, install the directory.

### Hot reload

`tools.hot_reload = true` starts `skeletonkey/skills/watch.py` inside the MCP server's
lifespan: on any change under a skills directory it calls `Toolkit.sync_skills()` — the
same `_sync_skill_tools` the build used — and sends `notifications/tools/list_changed`
when the advertised set actually moved. `watchfiles` is an **extra** (`[watch]`); when it
is absent the watcher returns `{"watching": false, "reason": "watchfiles is not installed",
"install": "pip install 'skeletonkey-toolforge[watch]'"}` instead of failing to start.
Everything below the tool level stays honest without it: `tk.sync_skills()` is callable by
hand, and `sk skills install` re-syncs on its own.

## Working examples in this repo

| Skill | Declares | Notes |
| --- | --- | --- |
| `fs-safe-refactor` | — | multi-file rename/patch discipline; `references/patch-strategies.md` and `references/undo-and-journal.md` carry the detail so the body stays small |
| `vcs-git-safely` | — | driving git from an unattended session: the read-before-write table, the recovery ladder (rung 0 to a dangling commit), and `references/recipes.md` for the `argv`-shaped call per task |
| `python-env-bootstrap` | — | interpreter and dependency environment work; `references/windows-vs-posix.md` gives each step twice because the layouts and the failure modes differ, and `references/lockfiles.md` says what each lock file entitles you to conclude |
| `shell-crossplatform` | `shell.selftest`, `shell.quote_check` | the two worked examples of a compiled tool: `shell.selftest` runs `scripts/selftest.sh` (and the `.ps1` sibling for PowerShell dialects) with `args_via = "none"` and stays `advertised = false`; `shell.quote_check` is an inline `handler_body` over `argv_json`, advertised, and its rules are the skill's own anti-patterns table turned into code |

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
6. Give every property a place in the script. A property the binding does not deliver is
   a compile error (`args_via = "none"` plus a schema is refused for exactly that reason),
   and `dialect` is reserved for choosing the interpreter.
7. `sk skills match "<task you had in mind>"` must return your skill before you commit it.
8. `python -m skeletonkey.cli skills load <name>` and read the rendered block — that is
   all the model will see unless it asks for more.
9. Then install it into a scratch workspace and call the tool for real
   (`sk skills install <dir> --dry-run`, then without it). A tool that has only been read
   is not a tool: the argv, the encoding and the exit codes are the parts that break.
