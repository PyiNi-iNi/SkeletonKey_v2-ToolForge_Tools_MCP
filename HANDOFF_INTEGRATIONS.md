# HANDOFF — Integrations (P5.3 loader made real + P5.5 closeout + doctor probe-failure fix shipped → P5.6/P6)

Session `arena/01a082ca-skeletonkey-v2-toolforge-tools` · 2026-09-08.
Written after landing the integration rest on top of merged `main` (`5d582ff`,
carries P5.1–P5.5 incl. the previous integration session's PR #9, plus the P6
onboarding slice and the P6 + Release 0.2.0 slice): the `sk doctor`
probe-failure bug fixed with regression tests, the P5.3 dynamic loader made
real (fix + the missing test coverage), and the P5.5 validation closeout
(secret source scan + `min_engine` gate, tracker decision). This is the
**integration-track** handoff — the P6-rest handoff (`HANDOFF.md`) is the
operator-track one (its state table was refreshed for accuracy this session).
Read `PLAN.md` for the roadmap, `docs/TOOL-CONTRACT.md` for the tool contract,
`IMPLEMENTATION_TRACKER.md` for phase status; this file is the *transfer* —
state, next steps, landmines. Standing constraints are collected in §7. It
supersedes the previous integration handoff.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode**
(repo-cloned sandbox, bash + file tools, auto-saved turns). Not attributable
to a single base model: Arena's Agent Mode draws on many (Claude, ChatGPT,
Gemini, Grok, Qwen, Kimi, …), and no specific one was recorded or should be
assumed. Everything below was measured against the merged tree at handoff,
not remembered: re-measure before you restate it.

---

## 1. State on handoff

| | |
| --- | --- |
| Branch | `arena/01a082ca-skeletonkey-v2-toolforge-tools` — pushed; **PR OPEN against `main` at handoff** (house flow: `--merge`, keep branch) |
| `main` | `5d582ff` (merge of PR #9 = the previous integration session). This branch adds 4 commits: doctor probe-failure fix, loader fix + tests, P5.5 closeout, test-isolation fix |
| Test suite | **795 passed, 3 skipped, 1 xfailed in ~80 s**; ruff clean (`ruff check .`) |
| Engine surface | **77 registered / 76 advertised** at `full` (unchanged; nothing this session is a registry tool — the loader/validation work is engine plumbing + the existing `integrations.validate` tool) |
| Integrations | **9 registered** (`simple-demo`, `sqlite`, `postgresql`, `mongodb`, `redis`, `aws-s3`, `requests`, `github` 18 tools, `stripe` 12 tools) — unchanged; the 5 SDK-gated ones still need their SDKs to load |
| Doctor | `sk doctor` → **`ok: true` in-repo** (was going red after any suite run — see §2.4); live `mcp.stdio` probe answers in single-digit ms |
| Version | `0.2.0` (shipped by the P6 + Release session, PR #7) |
| CI | still un-landed — `.github/` push rejected by App permissions (unchanged blocker; no `.github/` in the tree) |
| Tracker | `IMPLEMENTATION_TRACKER.md` P5.5 → 🟢 SHIPPED (core) with an explicit deferred list |

## 2. What this session shipped

**`sk doctor` probe failures are surfaced, not swallowed (the handoff's logged
latent bug, `diagnostics.py:176`).** The `mcp.stdio` check expanded the probe
rep into `**row` *and* passed `error=error` explicitly; a failing probe's rep
carries `error`, so the duplicate kwarg raised `TypeError` **exactly when the
probe failed** — masking the stderr tail the doctor exists to surface. Fix:
`row` excludes `error` (explicit pass kept, so the success row still carries
`error: null`; schema unchanged). Regression tests fail with the documented
TypeError against the pre-fix line (verified by re-introducing the bug): one
with a monkeypatched failing probe (asserts `error` + `stderr_tail` surfaced,
all other checks intact, stable order), one through the real failure path
(dead `python`).

**The P5.3 dynamic loader is real now (handoff item 3: it had no tests).**
Writing the tests surfaced three real gaps, all fixed:
(a) `IntegrationRegistry` had no `has_integration()` or
`register_integration()` — both called by `load_from_path` /
`load_from_module`, so **every file/module-based load failed with
`AttributeError` masked inside `LoadResult.errors`**. Both added
(`register_integration(cls, info)` mirrors `register()` expressed over an
`IntegrationInfo`; the decorator path gained a matching `conflicts`/`metadata`
kwarg). (b) The `Integration.info` property **dropped `conflicts`** (no
class-attribute convention existed), so declared conflicts never reached
`_check_conflicts`; the `_integration_conflicts` convention was added and
wired through all three registration paths. (c)
`IntegrationManager.reload_integration` called `loader.load()`, which
short-circuits on an already-loaded name — the reload **silently reused the
old instance and ignored the new config**; it now uses the loader's own
`reload()`, which unloads first.

**P5.5 closeout (handoff item 4: decided).** Two cheap, side-effect-free
checks joined the harness (stable codes, visible through the existing
`integrations.validate` tool):
- `secrets` — static scan of each integration's **own module source** for
  secret-looking literals. Reuses the redaction table via `redact_text` (no
  second pattern set to drift) but **only the unambiguous provider-key labels**
  (`stripe`, `aws_key`, `gh_token`, `github_pat`, `slack`, `discord`, `jwt`,
  `anthropic`, `openai`, `google`, `hf`, `pem`): the output-tuned
  context-dependent patterns (`kv_secret`, `bearer`, `conn_str`, `url_creds`,
  `cli_flag`, `auth_header`, `azure`) were measured to fire on ordinary source
  — 16 false positives across the built-ins — so they stay on the audit-text
  path only. Findings are **warnings** with label + line; the matched value is
  never echoed into the report (it would just re-leak into the report). A
  suite test pins the zero-false-positive guarantee on every built-in.
- `compatibility` — `metadata["min_engine"]` (semver) must be satisfiable by
  this engine version (numeric-core comparison); unparseable = warning,
  unsatisfied = error. This needed a `_integration_metadata` class convention
  (the `info` property dropped `metadata` just like it dropped `conflicts` —
  same bug class, third field).

`IMPLEMENTATION_TRACKER.md` records the decision: P5.5 is SHIPPED for the
delivered items; **explicitly deferred to P5.6/P6** — runtime secret scanning
of live tool *output* (needs a side-effect-gated live harness), the cross-SDK /
OS compatibility matrix (needs the gated SDKs installed + the Windows CI
runner), and pack upgrade testing (`upgrade_pack` exists and reload is now
coherent, but vN → vN+1 migration is untested end to end).

**The suite stopped leaving engine state in the repo working tree.** The
conftest contract is "tests never touch the repo working tree", but three
fixtures built toolkits with the repo as cwd and the default state dir:
`test_docs` (module-scoped toolkit), `test_mcp_stdio` (skill-pack server
spawned with `cwd=REPO`), `test_skills` (bare `build()`). Every full-suite run
left a **partial `.sk`** (journal/shell/ledger created, spill lazily absent) —
exactly the partial-state smell `sk doctor` reports, so doctor went red
in-tree after any suite run. Fixes: `SKELETONKEY_STATE_DIR` → tmp for the
server spawn (cwd stays the repo — `skills/` must be on the discovery path),
state → module-scoped temp dir for `test_docs`, temp cwd + explicit
`skills.dirs` for the `test_skills` registry check. Verified: full suite
leaves no `.sk`; `sk doctor` → `ok: true` in-tree.

## 3. What is NOT done (integration rest, in order)

1. **Merge this PR** — OPEN, mergeable, unreviewed at handoff. House flow:
   `gh pr merge <n> --merge`, keep the branch.
2. **P5.6 (docs & examples)** — the next integration slice: an integration
   **development guide** + testing guidelines (mirror TOOL-CONTRACT §8).
   Write from the code as it stands now: the full registration surface
   (including the new `conflicts`/`metadata` conventions), the
   discovery→registration contract (discovery finds *metadata*, registration
   provides the *class* — loading a discovered-but-unregistered integration
   fails with an explicit "class not found", not a crash), the validation
   codes as the CI-gate list, and the thread-safety rule (engine runs handlers
   on a pool).
3. **P6 (SDK/API framework)** — the `RestIntegration` base class that
   auto-generates tools from a `base_url` (absorbs `github.py`/`stripe.py`'s
   `_request`/`_api`/`_paged` scaffolding; only the auth header, body
   encoding, and error mapping differ per provider). With the loader and
   validation now real, a generated integration gets load + validation for
   free.
4. **The deferred P5.5 items** (see the tracker): runtime output secret
   scanning, cross-SDK/OS compatibility matrix, pack upgrade testing.
5. **The 5 SDK-gated integrations** (aws-s3, mongodb, postgresql, redis,
   requests) still cannot *load* here — their SDKs aren't installed; only
   sqlite/simple-demo/github/stripe are proven callable end to end.
6. **Operator track** (see `HANDOFF.md`): CI (`ci.yml` + Windows runner)
   still permission-blocked on the GitHub App.

## 4. Next steps (in order)

1. Review + merge this PR (`--merge`, keep branch); pull `main` into the next
   session's branch.
2. P5.6: the integration development guide (deliverable list in the tracker;
   it is where the deferred-item *docs* land too).
3. P6: `RestIntegration` base — start by extracting the shared
   `_request`/`_api`/`_paged`/`_envelope` from github/stripe, prove both still
   pass their stub-server wire tests, then let a third provider (or a test
   fixture) be built from the base.
4. Land `ci.yml` once the App has `workflows` permission (unchanged blocker;
   local repro is `ruff check . && pytest -q`).

## 5. How things run here (operational)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" "mcp>=2.1,<2.2"
.venv/bin/pytest tests/ -q                       # 795 passed, 3 skipped, 1 xfailed, ~80s
.venv/bin/ruff check .                           # clean
.venv/bin/sk --json doctor                       # ok: true in-tree (flags BEFORE the subcommand)
.venv/bin/pytest tests/test_integration_loader.py tests/test_integration_validation.py -q
```

`sk` **global** flags (`--root`, `--json`, `--read-only`, `--cwd`) go before
the subcommand — `sk doctor --json` aborts (argparse), `sk --json doctor`
works. Hit this session; the tests spell it out.

Sandbox recycles still wipe `.venv` (recreate; the package must stay
pip-installed **editable** for the MCP wire tests). In this sandbox the
pytest **final summary line is swallowed** by the capture (output ends at
`[100%]`); the exit code is trustworthy and the status chars are countable
(`.` passed, `s` skipped, `x` xfailed) — that is how the counts above were
measured. **Push early and often** (the remote is the only durable record of
commits) and verify `wc -l` after editing large files — see the
file-truncation landmine in §8.

## 6. Ideas (honest, none decided)

- `validate_registry()` as a **CI gate**: all-valid + zero `secrets` findings
  as a check (the harness is side-effect free and fast; CI itself is still
  blocked, but the gate is the one line).
- `IntegrationRegistry.unregister(name)` as public API — the tests currently
  pop the private `_integrations` dict for cleanup; pack uninstall (P6) needs
  the real thing.
- Carried from the previous handoff, still open: doctor's *sanitized*
  projection as an agent-visible health query (check `registry.stats` first);
  `sk export openai-tools` (P7); a reverse-proxy auth example for the http
  transport in CONNECT-A-HOST.md.
- The `field(default_factory=list)` idiom used for `Integration`'s class
  attributes is a codegen holdover (it leaves actual `DataclassField` objects
  on unregistered classes). A cleanup could normalize it to plain defaults —
  do it deliberately, with the `info` property as the tripwire.

## 7. Standing constraints (carried, reaffirmed)

- **Licensing/identity frozen:** Apache-2.0, authorship "Dime", README title +
  tagline unchanged. No relicense/retitle/author-tidying without the owner.
- **Python 3.11+, zero mandatory deps** (ADR-0001) — enforced by
  `tests/test_core_purity.py`. **SDK integrations stay stdlib-`urllib` with
  the official SDK as an optional accelerator via
  `importlib.util.find_spec`** — do not regress to a hard `import requests` /
  `import boto3` at module top.
- **Windows + Linux + macOS first-class; PowerShell not optional.** Threads,
  not `select`; no `localhost`-only assumptions in tests.
- **Primary consumer is the bespoke autopilot loop; MCP surface ships and
  stays honest.** A remote error code is never re-wrapped; no silent tool
  reordering; reports are versioned data (`sk.doctor/1`, `sk.wire/1`).
- **The suite never leaves engine state in the repo working tree** (conftest
  contract — now actually kept: any fixture that builds a toolkit with the
  repo as cwd must redirect state; doctor's partial-state check will catch
  violations the way this session's did).
- **House rule for new tools:** TOOL-CONTRACT section + skill-guidance entry +
  wire-level test. Spec-first + 3-ish chunks + explicit `git add`;
  `.github/` untracked.

## 8. Landmines (measured this session; old ones still bite)

- **The `Integration.info` property carries only fields with a class-attribute
  convention** (name/title/version/type/category/platform/description/author/
  license/keywords/dependencies/capabilities/**conflicts**/**metadata**).
  Registry lookups build a dummy instance and read `.info`, so any
  `IntegrationInfo` field added to the base dataclass but NOT to the
  convention is silently lost on the registry path. Adding one means
  touching all three registration paths (`register`, the decorator,
  `register_integration`) plus the property.
- **The redaction table's context-dependent patterns must never run over
  source code** (16 false positives on the built-ins, measured); the source
  scan is `_SOURCE_SCAN_LABELS` only. If you add a provider-key pattern to
  `core/redact.py` for the audit path, decide deliberately whether it belongs
  in the source set too.
- **The `edit` tool truncated a 1662-line file to its first 502 lines
  mid-session** (a failed fuzzy edit; the file recovered from HEAD +
  re-applied edits, but for one commit's worth of work it was gone from the
  working tree). For multi-edit batches on large files: one scripted replace
  with occurrence-count asserts, verify `wc -l` / import after, and keep the
  work pushed as soon as any chunk is green.
- **pytest's summary line is swallowed in this sandbox's capture** (output
  ends at `[100%]`); trust exit code + status-char counts, not the summary
  line.
- **`sk` global flags before the subcommand** — `sk --json doctor`, never
  `sk doctor --json`.
- **The test-fixure state trap:** a toolkit built with repo cwd writes a
  *partial* `.sk` (journal/shell/ledger eagerly, spill lazily) → doctor
  reports the repo unhealthy. Three fixtures did this before this session;
  the redirect patterns (env `SKELETONKEY_STATE_DIR`, `state.dir` override,
  temp cwd + `skills.dirs`) are in the test files.
- **mcp: keep 2.1.1** (house pin — snake_case result models,
  `streamable_http_client`); the MCP wire tests spawn `sys.executable -m
  skeletonkey.mcp` without PYTHONPATH, so the package must be pip-installed
  editable or they fail `server exited early (code 1)`.
- **Integration tool-id → handler mapping is `<name>.<suffix>`** with
  dots/hyphens → underscores; an id whose namespace ≠ the integration name is
  unmappable (the bridge silently skips it). Keep new ids `<name>.*`.
- **The engine runs handlers on a thread pool** — stateful integrations must
  be thread-safe (sqlite is the reference: `RLock` + `check_same_thread=False`
  + serialized execute/commit/fetch).
- **`sqlite.create_table` takes `schema`** (a `{col: type}` dict), *not*
  `columns`.
- **`integrations.validate` flags `dependencies` errors** for boto3/pymongo/
  psycopg2/redis/requests (not installed) — correct behavior; the tests
  assert it conditionally so they hold with or without those packages.
- **`registry.list_integrations()` returns `IntegrationInfo`, not names**;
  `get_integration_class(name)` takes the *name string*.
- **Loader API:** `Integration` exposes `initialize()`/`check_health()`/
  `cleanup()`, *not* `load()`/`health_check()`/`unload()`; `IntegrationInstance`
  has **no `name` field** — construct with `info=`/`config=`/`status=`/
  `instance=`. A discovered-but-unregistered integration fails `load()` with
  an explicit "class for X not found" — that is the contract, not a bug.
- **Doctor state semantics:** fresh (never-ran) workspace is *healthy*;
  partial state is the reported smell. Don't "fix" the fresh case — the
  wire→doctor first-run flow depends on it.
- Old, unchanged: `SkeletonKeyError.err.code` is the string `"BAD_ARGS"`;
  `registry.all()` is a method; `AdSnapshot` has `.tokens`/`.digest`;
  `test_docs.py` is the docs police (CONFIG_RE has no file-extension escape);
  the BURST table in `test_policy_property.py` must name every mutating tool;
  the watcher test is pinned (don't un-pin without `stop_after`); uvicorn
  can't nest in `asyncio.run`; the streamable-http path is `/mcp`; pytest
  must run from the repo root. See `HANDOFF.md` §8 for the full operator-track
  list.

## 9. Where things live (pointers, not contents)

- `skeletonkey/diagnostics.py` — the `mcp.stdio` check (probe-failure fix) +
  `probe_stdio()` (thread-pumped JSON-RPC).
- `skeletonkey/integrations/__init__.py` — `IntegrationRegistry` (incl.
  `has_integration` / `register_integration(cls, info)`), the
  `_integration_conflicts` / `_integration_metadata` conventions,
  `IntegrationManager` (registry + dynamic wiring, `reload_integration` now
  via the loader's `reload()`).
- `skeletonkey/integrations/loader.py` — P5.3 dynamic loader
  (`load_from_path` / `load_from_module` / `load` / dependency + conflict
  gates).
- `skeletonkey/integrations/validation.py` — P5.5 harness; new `_check_secrets`
  (`_SOURCE_SCAN_LABELS`) and `_check_compatibility` (`_version_core`).
- `skeletonkey/core/redact.py` — the single secret-pattern table (audit-text
  path; the source scan reuses it via `redact_text`).
- Tests: `tests/test_integration_loader.py` (9), `tests/integration_helpers.py`
  (disposable single-file integrations with guaranteed registry/`sys.modules`
  cleanup), `tests/test_integration_validation.py` (20, incl. the new
  secret-scan + min_engine tests), `tests/test_doctor.py` (12, incl. the two
  probe-failure regressions).
- Docs: `IMPLEMENTATION_TRACKER.md` (P5.5 SHIPPED + deferred list);
  `docs/TOOL-CONTRACT.md` §8 (the `integration` tool-source row).
- `HANDOFF.md` — operator track (P6 rest: CI blocker, carry-over state).
