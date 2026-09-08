# HANDOFF — Integration hardening and P7 exploration

Session branch: `arena/01a08316-skeletonkey-v2-toolforge-tools`  
Latest commit: `ae461c9` — `Fix source checkout doctor and remote wiring`  
Date: 2026-09-08  
Status: clean working tree; changes pushed to the session branch.

This handoff supersedes the older integration-track notes for the current
working state. It records what is shipped, what was explored, what QA found,
and the safest next slices. The repository remains Apache-2.0, authored as
Dime, Python 3.11+, and zero mandatory runtime dependencies.

## 1. Current quality gate

Measured in the sandbox after installing the declared development extras,
including `mcp>=2.1,<2.2` and `pytest-asyncio`:

- Full suite: **802 passed, 2 skipped, 1 xfailed**
- Collected tests: **805**
- `ruff check .`: clean
- `python -m compileall skeletonkey tests`: clean
- `pip check`: clean
- `git diff --check`: clean
- Working tree: clean

The two skips and one xfail are pre-existing/declared test conditions. The
full suite's output summary is swallowed by this sandbox, so the counts above
were derived from the collected test inventory and status characters; exit code
is authoritative.

## 2. What this track shipped

### P5.6 — documentation

- `docs/INTEGRATION-DEVELOPMENT.md`
- `docs/INTEGRATION-TESTING.md`

The documents cover registration, discovery versus registration, class-not-found
loading failures, `conflicts`/`metadata`, validation codes, optional SDKs,
thread safety, stub-server tests, cleanup, and MCP/engine wiring.

### P6 — REST framework

- `skeletonkey/integrations/rest.py` adds `RestIntegration`.
- Shared stdlib `urllib` transport handles URL/query construction, JSON decode,
timeouts, HTTP envelopes, error normalization, and common pagination.
- GitHub and Stripe now subclass the base while retaining provider-specific auth,
body encoding, error mapping, rate-limit metadata, request IDs, and pagination.
- `DemoREST` is a third base-derived test fixture.
- Safe retries are opt-in through `config.retries` and `config.retry_backoff`.
Only idempotent methods (`GET`, `HEAD`, `OPTIONS`) retry transient `429`/`5xx`
responses; writes are never retried.

### P5.5 exploratory hardening

These are deliberately small, opt-in implementations rather than claims of a
complete security product:

- Runtime integration output scanning via
  `config={"scan_output_secrets": True}`. It recursively uses the existing
  `redact_obj` table and defaults off to preserve provider payload semantics.
- `compatibility_matrix()` reports declared OS/platform support, required SDK
  availability, current runner OS, and `ready`/`blocked` state. It is a CI
  report generator, not an emulator of another OS.
- Local pack v1 → v2 upgrade coverage verifies replacement and persisted state.
- `IntegrationRegistry.unregister_integration()` is now public and pack cleanup
  no longer needs a private registry deletion API.

### QA fixes

Two real integration defects were found during the complete suite run and fixed
in `ae461c9`:

1. `sk doctor`'s live stdio probe failed from a source checkout when its child
   process ran in a temporary cwd. The probe now prepends the checkout package
   root to `PYTHONPATH`.
2. Remote MCP tools vanished from the wire surface when the remote child ran
   from a separate root. The stdio connector now explicitly passes the parent
   environment to the child, preserving import paths and configuration.

The doctor, direct probe, and remote wire regression tests now pass.

## 3. Exploratory P7 implementations and boundaries

P7 is a frontier phase, not a completed feature set. The current branch has
implemented the supporting seams below, but not the full P7 acceptance gate:

- `ToolManifest` already contains `parallel_safe`, `stateful`, `reversible`,
  provider, requirement, and risk metadata. These are the intended inputs for a
  future planner/DAG executor.
- The registry already supports provider receipts, tiered advertisement, remote
  source metadata, and dynamic expansion. These are the foundation for remote
  targets and tool export.
- Remote MCP enrollment is real and wire-tested, but it is not yet a general
  SSH/SFTP/Docker target adapter.
- Runtime output redaction is an exploratory safety seam, not a complete
  side-effect-aware secret policy. It is opt-in and only applies at the
  integration `call_tool` boundary.
- `compatibility_matrix()` is an exploratory CI artifact generator; actual
  Windows/macOS execution still requires runners.

No claim should be made that P7's remote-target, self-authored-tool,
subagent, parallel-DAG, or OpenAI/Anthropic export acceptance criteria are
complete.

## 4. Remaining work, ordered

### P7 first slices

1. **Tool export** — implement `sk export openai-tools` from the live registry,
   with schema validation and no secret-bearing metadata.
2. **Planner/DAG exploration** — use `parallel_safe` and `stateful` to produce
   a read-only plan before executing; refuse conflicting mutations rather than
   applying last-writer-wins.
3. **Remote target spike** — begin with read-only Docker or SSH target
   capability probing; keep the journal local and make before-images explicit.
4. **Self-authored quarantine** — load a generated tool only in a subprocess,
   clean environment, restricted scratch root, reduced timeout, and explicit
   promotion after successful calls.
5. **Subagent boundary** — restricted task-tier/read-only defaults, compact
   envelope, and ledger tail only.

### P6/P5.5 follow-up

- Install optional SDKs in CI and run live stub/integration coverage for the
  five gated integrations.
- Add Windows and macOS runners to the compatibility matrix.
- Add dependency audit and release artifact checks.
- Narrow broad exception handlers where a specific error boundary can be
  demonstrated without weakening cleanup or diagnostics.
- Add generated integration API reference and templates.

### Infrastructure blocker

The GitHub App previously rejected `.github/workflows/ci.yml` changes because
it lacks workflow permission. Do not ask for credentials in the session. Once
permissions are refreshed, add the workflow on this branch and run the matrix
against Python 3.11/3.13 and Linux/Windows/macOS.

## 5. Operating commands

```bash
# Recreate the sandbox environment when needed
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]" "mcp>=2.1,<2.2"

# Quality gate
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/sk --json doctor

# Integration-focused gate
.venv/bin/pytest -q tests/test_integration_*.py tests/test_rest_integration.py
```

Global `sk` flags go before the subcommand: use `sk --json doctor`, not
`sk doctor --json`.

## 6. Landmines

- Do not make optional SDK imports mandatory at module import time.
- Keep stdlib `urllib` as the fallback transport.
- The engine calls handlers on a worker pool; integration instances must be
  safe for concurrent calls and must not keep request-local mutable state.
- The MCP child must inherit an importable environment when launched from a
  source checkout or a separate remote root.
- Do not retry non-idempotent writes in `RestIntegration`.
- Do not echo matched secret values in validation findings or runtime reports.
- Tests must redirect state and never leave `.sk` artifacts in the repository.
- Do not use `localhost`-only assumptions in cross-platform tests.
- Keep P7 explorations opt-in and explicit until their wire/security contracts
  are documented and tested.

## 7. Commit trail for this track

- `044d697` — Add integration guide and REST integration base
- `8242d9c` — Migrate GitHub and Stripe to REST base
- `5bcff2f` — Track REST base integration milestones
- `f7c9435` — Add safe REST retry policy
- `e0c559b` — Add opt-in runtime integration output scanning
- `eccdb83` — Cover local integration pack upgrades
- `89a479b` — Add integration SDK and OS compatibility matrix
- `dbd9e9c` — Expose public integration unregister API
- `ae461c9` — Fix source checkout doctor and remote wiring
