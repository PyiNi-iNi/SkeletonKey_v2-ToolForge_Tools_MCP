# HANDOFF — SkeletonKey / ToolForge v2

Session `arena/01a055ea-skeletonkey-v2-toolforge-tools` · Rotterdam (America/Chicago) ·
**2026-08-31**. Written by the agent that shipped P0–P2, for the session that starts P3.
Read `PLAN.md` for the roadmap and `docs/` for the contracts; this file is the *transfer* —
state, decisions, landmines, and what to do first.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned sandbox,
bash + file tools, one long session, auto-saved turns). It is not attributable to a single
base model: Arena's Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen, Kimi, …),
and no specific one was recorded or should be assumed. Everything below was verified against
the tree, not remembered: re-measure before you restate it.

---

## 1. State on landing

| | |
| --- | --- |
| PR | **#1** `arena/01a055ea-…` → `main`, merged with `--merge` (no branch delete) |
| Branch head at handoff | `f094f54` (+ this doc's commit) — 10 commits over `e6e0452` |
| Surface | 35 tools registered / 33 advertised / **2 424** advertisement tokens / digest `fc1ca30868f2e469` |
| Skills | 4 packs discovered, 2 synthesized tools, 0 load errors |
| Tests | **495 passed, 2 skipped, 1 xfailed** in ~23 s (`pytest -m "not slow"`), 15 test modules |
| Lint | `ruff check .` clean |
| Code / docs | 11 738 lines in `skeletonkey/`; 2 235 lines of docs (plan, 4 contract docs, 7 ADRs, README) |
| Phases | P0 ✓ P1 ✓ P2 ✓ (skills → tools, install/uninstall, hot reload) → **P3 is next** |

`main`'s history ends at the merge commit that carried this file; `git log --merges --oneline -1`
gives the sha if you need to cite it.

## 2. What P2 actually is (one paragraph, because it will be misrepresented)

A skill pack may contain `tool.toml` with `[[tool]]` tables. `skeletonkey/skills/compiler.py`
turns each into a real `ToolManifest` whose handler runs **one script** through the shell
executor (`skeletonkey/shells/execute.py::run_script`, shared with `shell.run`). It is a
manifest plus a subprocess — never imported Python, never `eval`. Values bind to **argv** via
`args_via` (`flags` / `argv_json` / `stdin_json` / `none`) and therefore never meet script
text. Sandbox, budget, approval, ledger, `expects` parsing and the envelope all apply
unchanged. 35 compile-time refusals (31 naming the field, 22 adding an advice line) turn
"callable but broken" into "not registered, and here is why" — a bad declaration is a load-error
row in `skills.list`, visible with path + reason + advice. `skills.install` is gated by
`skills.allow_install` (default **false**, and while false the tool is not even advertised);
`dry_run` answers through the closed gate so a plan can be reviewed without the privilege.
`skills.uninstall` is declared `destructive` because it deletes through `fs.delete`, and it
refuses while a job whose `owner` ends with `skill:<name>` is running (job ids in `details`).

`docs/SKILLS-SPEC.md` §tool.toml is the author-facing contract; `docs/TOOL-CONTRACT.md` §7b is
the envelope a skill tool returns; `docs/SECURITY-MODEL.md` §"Skills: adding capability at runtime" is the threat story.

## 3. Decisions worth keeping (and why), i.e. do not "simplify" these

- **`dialect` is the interpreter selector and is reserved.** A tool may not both pin
  `dialect` and declare a `dialect` property: the caller's arg used to override the pinned
  interpreter and produce `MISSING_SHELL`. `shell.quote_check` renamed its input
  `target_dialect` for this reason; the compiler refuses the pair.
- **Skill handlers call `run_script`, not `engine.call("shell.run")`.** Going through the
  tool double-charges approval and writes two ledger rows for one script.
- **Scripts are inlined into the payload** (`handler_script` read from inside the pack,
  extension-allowlisted, size-capped) because skill dirs sit outside the sandbox; `cwd` is the
  workspace, and `env_mode` defaults to `clean` with a bootstrap allowlist plus caller `env`.
- **`install` copies are journalled** under `task_id="skill-install:<name>"`, so
  `fs.undo_task` reverses an install; caps are 24 files / 512 KiB each / 2 MiB total,
  symlinks skipped. `git_ref` answers `NOT_IMPLEMENTED` naming P6 — a network fetch and a
  trust decision do not belong with a file copy.
- **Risk ceiling for a synthesized tool is `write`**; `destructive = true` in a `[[tool]]` is
  refused. Conversely, do not launder a deleting tool down to `write` — that is why
  `skills.uninstall` carries `destructive`.
- **`dry_run` is honoured only if the tool declares the property** — it is the author's
  promise, and `Engine._previewable` reads the schema rather than guessing.
- **One sync function for every mutation path** (`Toolkit._sync_skill_tools`), so a
  hand-authored pack dropped in `skills.dirs`, an install, an uninstall and a hot reload all
  converge.
- **`watchfiles` stays an optional extra** (P2 criterion 5): `tools.hot_reload` reports why it
  cannot run instead of crashing. Absent-the-dependency is the tested path here.
- **Docs are claims, so they are linted.** `tests/test_docs.py` resolves every documented
  `tool.id {prop}` span, bare `` `fs.x` ``-style name, and `| CODE |` table row against the
  live registry/config/error taxonomy, and requires any tool the roadmap still owes to cite its
  phase inline. PLAN.md is out of scope on purpose; this file is too (it cites deferred names).

## 4. Landmines — each cost real time to discover

- `fs.rm` is **not** a tool id. The tool is `fs.delete {path, recursive, dry_run}`; `rm` is
  only the `sk fs rm` CLI verb. Three docs carried the wrong name.
- `skills.list` takes only `refresh`. Writing `skills.list {errors}` in prose reads as a call
  shape and fails the docs lint — `errors` and `skill_tools` are *result* keys, so describe
  them in prose. Same trap: `registry.load_errors` is not a resolvable name (say "the
  registry's load-error list").
- `ToolError` has no `.next_actions` (they live on `ToolResult`), and `SkeletonKeyError` has
  no `.message` (use `str(exc)`, `exc.details`, `exc.code`). `error.to_dict()` is flat.
- `Engine._validate` short-circuits when a schema has no `properties`: never assert `BAD_ARGS`
  for a properties-less skill tool — declare a property.
- `RenderOptions` has no `argv`/`expects` (argv comes from `shells/base._extra_argv`);
  `ToolManifest` has no `tool.get`; the describe path is `registry.describe`.
- `sk --root X` does **not** change config discovery (that comes from `--cwd`). And in probes,
  a relative `skills.dirs` entry resolves against the config's cwd — pass an absolute path or
  you will debug `UNKNOWN_TOOL` forever.
- `tf.extractall(dest, filter="data")` is a `TypeError` on 3.11.0–3.11.3 (landed in 3.12,
  backported to 3.11.4). `fsx/journal.py::_extract_guarded` is the fallback; it validates
  every member before extracting, so a poisoned shadow archive is refused **whole** and stays
  retryable. Do not "clean it up" back to one call.
- `ShellRequest.on_timeout` (`kill-tree|kill-self|ignore`) exists in the runner but is
  unreachable from `shell.run`; `shell.kill_tree` (config) is the only dial. Skill prose was
  edited to stop promising a per-call knob.
- This session's tree also grew two bug fixes that arrived as *tests failing*, not as review
  comments: `McpBridge.resolve_name` cached its name map (a tool added after the first
  `tools/list` was advertised but `UNKNOWN_TOOL` when called — now rebuilt once on a miss), and
  the journal/tarfile one above. Treat "the acceptance test disagrees with my design doc" as
  the signal.
- Patch-script traps, if you keep working the way this session did (edit via python scripts in
  `/home/user/tmp/`): asserting on a line whose wrapping differs aborts the script *before any
  write*, so earlier edits silently vanish; nested `'''` inside a triple-quoted patch string
  terminates the outer one (write the spliced text to its own file instead); and a nested
  heredoc over-escapes backslashes.

## 5. Reproducing the environment

The sandbox's persisted snapshot excludes `.venv`, `/tmp`, `__pycache__`, etc. — so after any
recycle: **`/tmp` probes are gone and `.venv` must be rebuilt.** The repo itself persists.

```bash
python3 -m venv .venv
.venv/bin/pip install -q -e . pytest pytest-asyncio ruff "mcp>=2.1,<3"
.venv/bin/ruff check . && .venv/bin/python -m pytest -o addopts="" -q -m "not slow" --tb=short
```

Notes: interpreter here is **3.11.2** (so the journal's fallback extractor is what runs);
`mcp` 2.1.1 (2.x API: `MCPServer`, not `FastMCP`); **do not install `watchfiles`** — its
absence is the tested state; pyproject's `dev` extra also pulls mypy, which no test needs.
`pytest -q` alone prints no summary line because `addopts` already contains `-q` (double quiet);
use `-o addopts=""` when you want counts.

CLI smoke, no MCP client required (verified this session, README §"What is here" is that block):

```bash
PYTHONPATH=. .venv/bin/python -m skeletonkey.cli --root WS --cwd WS skills install ./my-skill --dry-run
PYTHONPATH=. .venv/bin/python -m skeletonkey.cli --root WS --cwd WS skills install ./my-skill
PYTHONPATH=. .venv/bin/python -m skeletonkey.cli --root WS --cwd WS call skill.my-skill.wordcount '{"path":"notes.txt"}'
```

## 6. Test map (which file proves which claim)

| File | Proves |
| --- | --- |
| `test_skill_synthesis.py` (39) | each `args_via` channel, each refusal, inlining, env/owner, install→real bash→call, restart survival, uninstall incl. live-job refusal, load-error-not-broken-tool, pwsh payload rendering, the two shipped skills' tools |
| `test_mcp_stdio.py` | raw JSON-RPC over a real subprocess: handshake, list shape, call/error paths, prompts, resources, clean exit, **install → `tools/list_changed` → listed → called → removed**, and the closed-gate refusal on the wire |
| `test_journal.py` | before-images across restart, mode/mtime restore, prune, poisoned `.tar` refused whole, in-tree symlink round-trip |
| `test_docs.py` | the doc claims listed in §3 |
| `test_skills.py` | loader/frontmatter/injection budgets, and that repo packs ship real, small, honestly-cited guidance |
| rest | envelope, contracts, engine/policy, fs ops, sandbox, dialects, runner, registry/config, ledger/redaction |

## 7. What to do first in the next session

1. **P3 — policy, safety, reversibility**, per `PLAN.md` §P3. Concrete entry points:
   `Engine._authorize` (precedence is `tools.disable` → deny → `dry_run` → approval → profile,
   and `docs/SECURITY-MODEL.md` is written from that order — change one, change the other),
   a policy engine with *reasons* on every refusal, per-tool rate limits, trash tiers for
   deletes, and `fs.redo` — note that neither `fs.redo` nor `policy.grant` is registered
   yet: they are only entries in `tests/test_docs.py`'s `PENDING` register, so *creating*
   either means deleting its `PENDING` entry in the same commit (the lint fails an unused one,
   and fails a cited-but-unphased one). Decide, and record in the ADR directory, whether
   `skills.allow_install` ever
   defaults to true; without a scoping policy it should stay false.
2. **Approval UX on MCP**: `APPROVAL_REQUIRED` currently returns a coded envelope. Real
   protocol elicitation is the P3 unlock for hosts that support it; keep the deny-with-reason
   default for stdio hosts with no UI.
3. **Cheap, high-trust polish** (half a day, do it before it rots):
   add `advice` to the 13 compiler refusals that lack it; validate `[[tool.examples]]` args
   against the tool's own schema at load time (that one check would have caught the
   `target_dialect` rename drift); expose per-call `on_timeout` on `shell.run` or delete the
   claim from `ShellRequest`'s docstring.
4. **Windows CI before P7.** `PLAN.md` §6 is a transcribable pipeline spec (four jobs:
   `core-constraint`, `test`, `smoke`, `audit`; `windows-latest` starts `continue-on-error`);
   a 3.12 job would also finally exercise the *native* `filter="data"` branch of the journal.
   Note: this repo's push token cannot create `.github/workflows/*`, which is why the file is
   specified rather than committed — someone with admin rights lands it.
5. `registry.alias` belongs to P5 (discovery), not P2 — shadowing stays refuse-or-override
   until then. `skills.match` stays lexical until the P5 router; the call shape will not change.

## 8. Ideas on the bench (mine, unstarted)

- Pack trust for P6: `skills.pin = { name = "sha256:…" }` so "installed" is distinguishable
  from "signed"; a `skill.lock` next to the pack, and `skills.install` refusing an unpinned
  source when pins exist.
- P4 exit gate as an eval: *how often a synthesized tool is called again after install* is
  direct evidence the `tool.toml` contract is learnable; that number belongs in the autopilot
  receipts, and it is the only honest measure of "dynamic tools" value.
- Discovery receipts (`exposed_results` / `withheld` / `stop_reason`) as the return shape for
  `registry.search` and `skills.match` in P5 — borrowed from
  `agentic-community/mcp-gateway-registry`'s dynamic-tool-discovery doc, already adopted for
  `details.gate`.
- `sk skills suggest "<task>"`: draft a `tool.toml` from the loaded guidance, so the compiler's
  refusals double as authoring advice.
- Token-budget regression: P2's whole dynamic surface cost +246 advertisement tokens
  (2 178 → 2 424) for five registered tools; that per-tool
  cost belongs in CI as a number, because R1 (context-window exhaustion) is the top risk.

## 9. Standing constraints from the human

- **License Apache-2.0 and "Dime" authorship are preserved**; README keeps the original title
  line and the "Dime's Custom Toolkit" tagline. Do not relicense, retitle, or "tidy" authors.
- Runtime is **Python 3.11+, zero mandatory dependencies**; the core must stay importable with
  nothing installed (`core-constraint` in the pipeline spec enforces it).
- Windows **and** Linux/macOS are both first-class; pwsh is not optional, and a claim about
  PowerShell must be backed either by a rendered-payload assertion or by a marked `win` test
  that self-skips off Windows.
- The primary consumer is a bespoke autopilot loop, so tools may be richer/stateful than
  generic MCP allows — but the MCP surface ships and stays honest (it is the second consumer).
- House rule every phase: a new tool ships with a section in `docs/TOOL-CONTRACT.md`, an entry
  in the skill guidance an agent will actually read, and a **wire-level** test. A feature that
  only works when called from Python is not done.
