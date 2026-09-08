# HANDOFF — Integrations (P5.4 wiring + GitHub/Stripe SDKs + P5.5 validation shipped → integration rest)

Session `arena/01a0824c-skeletonkey-v2-toolforge-tools` · 2026-09-08.
Written after landing the integration slice on top of merged `main` (`f495234`, carries
P5.1–P5.4): a green baseline, the P5.4 integration tools wired into the engine so they
are *actually callable*, two new zero-dependency SDK integrations (GitHub, Stripe), and
the P5.5 testing/validation framework. This is the **integration-track** handoff — the
P6-rest handoff (`HANDOFF.md`) is the operator-track one; both are live. Read `PLAN.md`
for the roadmap, `docs/TOOL-CONTRACT.md` for the tool contract; this file is the
*transfer* — state, next steps, landmines. Standing constraints are collected in §7.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned sandbox,
bash + file tools, auto-saved turns). Not attributable to a single base model: Arena's
Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen, Kimi, …), and no specific
one was recorded or should be assumed. Everything below was measured against the tree at
handoff, not remembered: re-measure before you restate it.

---

## 1. State on handoff

| | |
| --- | --- |
| Branch | `arena/01a0824c-skeletonkey-v2-toolforge-tools` — pushed to origin; **PR #9 OPEN against `main`, MERGEABLE, no review decision yet** |
| `main` | `f495234` (merge PR that carries P5.1–P5.4). This branch adds `18f4c0e` (baseline + wiring) + `bcce55e` (SDKs + validation) + this doc |
| Test suite | **780 passed, 3 skipped, 1 xfailed in ~71 s**; ruff clean (`ruff check .`) |
| Engine surface | **77 registered / 76 advertised / 6949 tokens** at `full`, digest `e90e3d836d87b79b` (one manifest gated, as before) |
| Integrations | **9 registered** (was 7): `simple-demo`, `sqlite`, `postgresql`, `mongodb`, `redis`, `aws-s3`, `requests`, **`github` (18 tools)**, **`stripe` (12 tools)** — 91 tool dicts total |
| Venv | repo `.venv`: **mcp 2.1.1** (house pin), pytest 9.1.1, ruff 0.16.6; package installed **editable** (`pip install -e .`). `requests`/`boto3`/`pymongo`/`psycopg2`/`redis`/`stripe` **not** installed |
| Callable today | `sqlite` (10), `simple-demo` (3), `github` (18), `stripe` (12) proven end-to-end through the engine; the 5 SDK-gated integrations register but won't load without their SDKs |

## 2. What this session shipped

**Green baseline (4 red tests fixed).** `IntegrationPackManager` no longer `mkdir`s
eagerly at init (`_ensure_root()` is lazy) — fixed the read-only/deny-wall violations.
`SecretStore` no longer leaks a warning to stderr on init (deferred logger warning) —
fixed the MCP stdio contract. BURST policy-property coverage for the 8 integration
mutators restored. Nested `ImportError` `from` clauses added across the integration
modules and the pre-existing 216 ruff violations cleared (now `ruff check .` is clean).

**P5.4 wiring — the tools are now callable, not just metadata.** New
`skeletonkey/integrations/bridge.py` converts each integration's `get_tools()` dicts
into engine `ToolManifest`s whose handler dispatches to the instance's `tool_<suffix>`
method. `Integration.call_tool()` added as the single dispatch point;
`IntegrationManager` gained `_tool_registry`/`register_tools`/`_unregister_tools`, wired
through load/unload/reload (both the registry path and the P5.3 dynamic loader path);
`toolkit.build()` passes the engine registry in. Tool-id convention:
`<integration>.<suffix>` → `tool_<suffix>`, dots/hyphens normalized to underscores
(`github.repo.get` → `tool_repo_get`, `stripe.payment_intents.create` →
`tool_payment_intents_create`). The P5.3 loader was also fixed — it called non-existent
`load()/health_check()/unload()`, now `initialize()/check_health()/cleanup()`, and it
constructed `IntegrationInstance(name=...)` against a fieldless dataclass. `simple_demo`
got real handlers; sqlite got thread-safety (engine runs handlers on a thread pool).

**GitHub SDK integration (18 tools, zero mandatory deps).**
`skeletonkey/integrations/categories/devtools/github.py` — stdlib `urllib` transport,
PyGithub optional (`importlib.util.find_spec`). Tools: `rate_limit`, `user.get`,
`repo.{get,list,contents,tags,branches}`, `releases.list`, `issues.{list,get,create,
comment}`, `pulls.{list,get,create}`, `search.{repos,code}`, `contents.update`
(base64-encodes content, percent-encodes paths). Token: secrets/config/`GITHUB_TOKEN`/
`GH_TOKEN`. Rate-limit envelope `_rate`; HTTP ≥400 → `E.REMOTE` with status + body.

**Stripe SDK integration (12 tools, zero mandatory deps).**
`skeletonkey/integrations/categories/devtools/stripe.py` — stdlib `urllib`,
form-encoded bodies (Stripe's encoding), stripe SDK optional. Tools: `balance`,
`charges.{list,retrieve,create}`, `customers.{list,retrieve,create}`,
`payment_intents.{create,retrieve}`, `invoices.list`, `products.list`, `prices.list`.
Key: secrets/config/`STRIPE_SECRET_KEY`/`STRIPE_API_KEY` (required — Stripe has no
anonymous access; missing key → `E.BAD_ARGS`). Request-id envelope `_request_id`;
typed Stripe error mapping (`stripe_code`/`type` in `E.REMOTE` details).

**P5.5 testing & validation framework.**
`skeletonkey/integrations/validation.py` — a side-effect-free harness over the already
imported registry: metadata (kebab-case name, semver), tool→handler mapping (the exact
bug class the bridge fixes), schema/risk/tier sanity + duplicate ids, dependency
importability, security (mutating tools should declare a capability), opt-in health.
Plus `benchmark_integration()` (get_tools latency) and `dependency_status()`. Re-exported
from the integrations package. New **`integrations.validate`** engine tool wires the
harness into the product surface. Documented the integration tool-source as the 5th
mechanism in `docs/TOOL-CONTRACT.md` §8. Tests: `tests/test_integration_validation.py`
(16) — harness invariants, bridge mapping, and stub-`http.server` wire tests for both
SDKs (token gating, base64 content, form encoding, error mapping, engine round-trip).

## 3. What is NOT done (integration rest, in order)

1. **Merge PR #9** — OPEN, MERGEABLE, unreviewed. House flow: `gh pr merge 9 --merge`,
   keep the branch.
2. **Latent bug noticed, not fixed** (`skeletonkey/diagnostics.py:176`):
   `add("mcp.stdio", bool(rep.get("ok")), error=error, **row)` where `row` still
   contains `error` — a duplicate-kwarg `TypeError` that fires *exactly* when the probe
   fails, masking the real stderr tail. Out of scope this session; leave a note.
3. **No test coverage for the P5.3 dynamic loader path.** The loader was made coherent
   but `load_integration_dynamic` / `load_from_path` / `load_from_module` / discovery
   have no tests; wire tests only exercise the registry `load_integration` path.
4. **P5.5 tracker's heavier items still PLANNED**: security scanning, compatibility
   testing, upgrade testing. Delivered: static validation + benchmark + engine tool +
   tests. The five SDK-gated integrations (aws-s3, mongodb, postgresql, redis, requests)
   still cannot *load* here — their SDKs aren't installed, so only sqlite/simple-demo/
   github/stripe were proven callable end-to-end.
5. **P5.6 (docs & examples)** and **P6 (SDK/API framework)** are the next phases; the
   P5.5 deliverable list also names GCP, Azure, Slack, Discord, SendGrid SDKs, the
   httpx/aiohttp clients, and the full aws-devkit — none built.

## 4. Next steps (in order)

1. Review + merge PR #9; `git pull` `main` into the next session's branch.
2. Fix `diagnostics.py:176` (exclude `error` from `row` or pass it once) + a
   probe-failure regression test that asserts the stderr tail is surfaced.
3. Add a P5.3 loader test: `load_from_path` on a temp module → tools registered → call →
   unload/reload; same for a discovery source.
4. Decide the remaining P5.5 tracker items: either extend `validation.py` (a `security`
   check that flags secret-looking literals in tool output, an upgrade/version check) or
   explicitly move them to P5.6/P6 in `IMPLEMENTATION_TRACKER.md`.
5. P5.6: integration development guide + testing guidelines (mirror TOOL-CONTRACT §8);
   P6: a REST base class that auto-generates tools from `base_url` (would absorb
   `github.py`/`stripe.py`'s `_request`/`_api`/`_paged` scaffolding).

## 5. How things run here (operational)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # sandbox recycles wipe .venv
# if the MCP wire tests misbehave, pin the house version:
.venv/bin/pip install "mcp>=2.1,<2.2"
.venv/bin/pytest tests/ -q                       # 780 passed, 3 skipped, 1 xfailed, ~71s
.venv/bin/ruff check .                           # clean
.venv/bin/pytest tests/test_integration_validation.py -q   # 16 integration tests, ~4s
```

Quick integration probe (no network): build a toolkit in a temp workspace, then
`engine.call("integrations.load", {"name": "sqlite"})` → `engine.call("sqlite.query",
{"sql": "SELECT 1"})` → `engine.call("integrations.unload", ...)`. For github/stripe,
point `config={"base_url": <stub>}` at a local `http.server` (see the test file).

## 6. Ideas (honest, none decided)

- A shared `RestIntegration` base (P6) that the GitHub/Stripe integrations subclass —
  both already implement the same `_request`/`_api`/`_paged`/`_envelope` envelope; only
  the auth header, body encoding (JSON vs form), and error mapping differ.
- `integrations.validate` could grow a `--fix`-like report (which findings are
  auto-fixable) and a CI gate (`validate_registry()` must be all-valid).
- Surface the validation report in `sk doctor` as an `integrations` check (mirrors the
  existing tools/skills rows) — the harness is already import-level and side-effect free.

## 7. Standing constraints (carried, reaffirmed)

- **Licensing/identity frozen:** Apache-2.0, authorship "Dime", README title + tagline
  unchanged. No relicense/retitle/author-tidying without the owner.
- **Python 3.11+, zero mandatory deps** (ADR-0001) — *enforced* by
  `tests/test_core_purity.py`. **SDK integrations must stay stdlib-`urllib` with the
  official SDK as an optional accelerator via `importlib.util.find_spec`** — this is how
  github/stripe are built; do not regress to a hard `import requests`/`import boto3` at
  module top.
- **Windows + Linux + macOS first-class; PowerShell not optional.** Threads, not
  `select`; no `localhost`-only assumptions in tests.
- **Primary consumer is the bespoke autopilot loop; MCP surface ships and stays honest.**
  A remote error code is never re-wrapped (SDK integrations raise `E.REMOTE` with the
  upstream code in `details`). No silent tool reordering.
- **House rule for new tools:** TOOL-CONTRACT section + skill-guidance entry + wire-level
  test. Done for the SDK integrations: TOOL-CONTRACT §8 got the "integration" tool-source
  row; wire-level tests live in `test_integration_validation.py`. Skill guidance is N/A —
  integrations are a dynamic tool source, no `SKILL.md` names them.
- **Spec-first + 3-ish chunks + explicit `git add`**; `.github/` untracked (CI still
  permission-blocked, unchanged).

## 8. Landmines (measured this session; old ones still bite)

- **Sandbox recycles wipe `.venv` *and* git commit objects; working-tree file edits
  survive.** Mid-session the baseline commit (`da8c02a`) vanished — the reflog showed a
  fresh `clone` + `checkout`, with the file changes intact but uncommitted. Re-committed
  as part of `18f4c0e`. **Push early and often — the remote is the only durable record of
  commits.**
- **MCP wire tests spawn `sys.executable -m skeletonkey.mcp` *without* PYTHONPATH**
  (doctor's `probe_stdio`, and the remote-server child). The package must be
  **pip-installed editable** (`pip install -e .`) or they fail `server exited early
  (code 1)` / missing `remote.demo.*`. `test_mcp_stdio.py` sets PYTHONPATH for its own
  client, which is why only doctor + the remote test failed.
- **mcp: keep 2.1.1** (house pin — snake_case result models, `streamable_http_client`).
  `.[dev]` declares `<3` and may resolve 2.2.x; pin with `"mcp>=2.1,<2.2"` if a fresh
  venv misbehaves.
- **Integration tool-id → handler mapping is `<name>.<suffix>`** with dots/hyphens →
  underscores. An id whose namespace ≠ the integration name is unmappable (the bridge
  silently *skips* it) — legacy `simple.*` / `aws.s3.*` ids violate this; `simple_demo`
  was rewritten to `simple-demo.*`, and `aws-devkit` is currently unregistered (boto3
  absent). Keep new ids `<name>.*`.
- **The engine runs handlers on a thread pool** — stateful integrations must be
  thread-safe. sqlite got `RLock` + `check_same_thread=False` + serialized
  execute/commit/fetch. postgres/mongo/redis have the same hazard once actually wired
  and loaded.
- **`sqlite.create_table` takes `schema`** (a `{col: type}` dict), *not* `columns`.
- **`integrations.validate` flags `dependencies` errors** for boto3/pymongo/psycopg2/
  redis/requests (not installed) — correct behavior, not a regression; the tests assert
  it conditionally so they hold with or without those packages.
- **`registry.list_integrations()` returns `IntegrationInfo`, not names**;
  `get_integration_class(name)` takes the *name string* (not the info object).
- **`get_tools_from_integrations()` is metadata-only** — engine dispatch goes through
  `IntegrationManager.register_tools/_register_tools` (load) and `_unregister_tools`
  (unload). Don't re-wire dispatch through `get_tools_from_integrations`.
- **Loader API mismatch (fixed):** `Integration` exposes `initialize()/check_health()/
  cleanup()`, *not* `load()/health_check()/unload()`; `IntegrationInstance` has **no
  `name` field** — construct with `info=`/`config=`/`status=`/`instance=`.
- Old, unchanged: `SkeletonKeyError.err.code` is the string `"BAD_ARGS"`;
  `registry.all()` is a method; `AdSnapshot` has `.tokens`/`.digest`; `test_docs.py` is
  the docs police (CONFIG_RE has no file-extension escape); the BURST table in
  `test_policy_property.py` must name every mutating tool; the watcher test is pinned
  (don't un-pin without `stop_after`); uvicorn can't nest in `asyncio.run`; the
  streamable-http path is `/mcp`; pytest must run from the repo root. See `HANDOFF.md`
  §8 for the full operator-track list.

## 9. Where things live (pointers, not contents)

- `skeletonkey/integrations/bridge.py` — tool-dict → `ToolManifest` bridge
  (`tool_suffix`, `build_manifest`); marks manifests stateful (`session`, `open_world`).
- `skeletonkey/integrations/__init__.py` — base classes + `Integration.call_tool`;
  `IntegrationManager` (registry wiring, `_register_tools`/`_unregister_tools`,
  `register_tools`); `__all__` re-exports the validation API.
- `skeletonkey/integrations/loader.py` — P5.3 dynamic loader (fixed); `_dep_name`/
  `_dep_optional` helpers; dependency importability via `find_spec`.
- `skeletonkey/integrations/validation.py` — P5.5 harness (`validate_integration`,
  `validate_registry`, `benchmark_integration`, `dependency_status`).
- `skeletonkey/integrations/categories/devtools/{__init__,github,stripe}.py` — the two
  new SDKs; `devtools/__init__.py` registers them.
- `skeletonkey/core/integration_tools.py` — `integrations.*` engine tools; added
  `integrations.validate` (last registered tool).
- `skeletonkey/toolkit.py` — `build()` passes `tool_registry=registry` to
  `IntegrationManager`.
- `skeletonkey/integrations/categories/databases/sqlite.py` — thread-safety reference
  implementation for stateful integrations.
- `tests/test_integration_validation.py` — 16 tests (harness + stub-server wire).
- `docs/TOOL-CONTRACT.md` — §8 "Adding a tool" now lists the `integration` source row.
