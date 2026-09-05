# HANDOFF — SkeletonKey / ToolForge v2 (P6 onboarding slice shipped → P6 rest)

Session `arena/01a06fe5-skeletonkey-v2-toolforge-tools` · 2026-09-05.
Written after landing the **instant-onboarding slice of P6** on top of merged `main`
(`7d57e31`, carries P5 + live.*): `sk wire` (auto-wire into MCP hosts), `sk doctor`
(live-proof diagnostics), the streamable-http transport made real, ADR-0001 enforced by
test, and the connect-a-host page. For the session that finishes **P6** (releases +
security pass + Windows CI). Read `PLAN.md` for the roadmap, `docs/` for the contracts;
this file is the *transfer* — state, next steps, landmines. It supersedes the previous
handoff; standing constraints are collected in §7.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned sandbox,
bash + file tools, auto-saved turns). Not attributable to a single base model: Arena's
Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen, Kimi, …), and no specific
one was recorded or should be assumed. Everything below was measured against the merged
tree at handoff, not remembered: re-measure before you restate it.

---

## 1. State on handoff

| | |
| --- | --- |
| Branch | `arena/01a06fe5-skeletonkey-v2-toolforge-tools` — this session's branch; **no PR opened yet** (house flow: open against `main`, `--merge`, keep branch) |
| `main` | `7d57e31` (merge PR #5 = P5). This branch adds `e571669` (sk wire) and `ef51dc5` (doctor + http + purity) + uncommitted docs/handoff chunk |
| Test suite | **733 passed, 3 skipped, 1 xfailed in ~57 s**; ruff clean (`ruff check .`) |
| Tools | **61 registered / 59 advertised / 6410 tokens** at `full`, digest `05c88f0f77b7fd74` — **unchanged** (wire/doctor are operator CLI, deliberately *not* registry tools, ADR-0015) |
| Venv | repo `.venv`: mcp 2.1.1, pytest 9.1.1, ruff, pyyaml, **watchfiles 1.2.0 present** (`.[dev]` default). Package installed editable (`pip install -e ".[dev]"`) |
| Doctor | `sk doctor` → `ok: true` in-repo; live `mcp.stdio` probe answers in ~1.5 s (58 tools in the scratch workspace — no skills dirs there) |
| Docs | ADR-0015 (onboarding is operator-side); `docs/CONNECT-A-HOST.md` (wire/doctor schema + per-host stanzas); PLAN §6 annotated with the shipped slice; README "Drop it into any project" section |
| CI | still un-landed (`.github/` push rejected by App permissions — unchanged blocker) |

## 2. What this session shipped

**`sk wire` — auto-wire (the "drop into any project" command).** `skeletonkey/wire.py`
(stdlib-only, instant, no Toolkit build): detects claude-desktop / claude-code / cursor /
vscode(+Insiders/VSCodium) / windsurf configs per platform, writes the `skeletonkey`
entry (`sys.executable -m skeletonkey.mcp`) with merge-not-rewrite semantics: foreign
servers and keys preserved, atomic write + one-generation `.sk-wire.bak` backup,
second run = `already`, `--remove` takes out exactly ours and refuses a hand-written
entry with the same name. JSONC configs (VS Code) are answered `needs-manual` with the
exact stanza to paste unless `--allow-jsonc`. `--project` writes `.mcp.json` /
`.cursor/mcp.json` / `.vscode/mcp.json` with `--root <project>` pinned and **never falls
back to the user's home config** (hosts without a project scope are skipped).
`--transport streamable-http` writes url stanzas for hosts that support them and refuses
claude-desktop with the reason. `--check`/`--dry-run` write nothing; `--json` machine
report (`sk.wire/1`).

**`sk doctor` — diagnostics with a live proof (P6 deliverable).** `skeletonkey/
diagnostics.py`: fixed-order checks — meta, config layers, roots writability, state dirs
(**fresh workspace = healthy**; *partial* state is the reported smell), tools
(registered/advertised/digest/gated/load-errors), skills, profile receipts, journal,
ledger (`Ledger.verify()` on the real file), **`mcp.stdio`: a live end-to-end probe**
(spawns the real server on a scratch workspace; initialize → tools/list → `fs.stat` on a
file it wrote; reader *threads* not `select`, so it runs on Windows), and a read-only
`wire` scan (which hosts are installed/wired). `--fix` = only the safe repairs (create
state/spill/journal dirs, retire stale profile cache), each reported. Diagnosis never
creates the operator's state as a side effect: the introspection build uses a **scratch
state dir**; assertable in `test_doctor.py`. Exit code mirrors report `ok`.

**streamable-http transport made real.** `skeletonkey/mcp/__main__.py` was a
`pragma: no cover` sketch that **crashed** (uvicorn.run inside asyncio.run). Now:
`serve_http()` runs *outside* `asyncio.run` (uvicorn owns the loop), `--host/--port`
args, listening banner on stderr. `sk mcp` forwards host/port. Wire-tested with the SDK
client over real HTTP (`tests/test_mcp_http.py`): initialize → list → `fs.stat` envelope
in `structured_content` → gates hold over http (`skills.install` absent) → unknown tool
is a tool error. No new dependency: `mcp` already requires uvicorn/starlette.

**Two honest drive-by fixes.** (a) `tests/test_skill_synthesis.py` watcher test hung the
whole suite whenever watchfiles was installed (the `.[dev]` default!): it entered a real
`awatch` loop with no `stop_after`. Fixed by pinning `watch.available → False` — the test
is about the cannot-run *report*, which is now deterministic in both states.
(b) `mcp/client.py` imported `streamablehttp_client`, which mcp 2.1.1 renamed to
`streamable_http_client` — http remotes would have crashed at connect; now accepts both
spellings.

**ADR-0001 enforced, not asserted (P6 deliverable).** `tests/test_core_purity.py`:
subprocess with `-S` + PYTHONPATH=repo-only imports the core and runs `sk wire --check`
and `sk doctor --no-probe`; fails if mcp/pydantic/watchfiles/jsonschema/yaml/uvicorn/
starlette/httpx/anyio is loaded or even importable.

**Docs.** `docs/CONNECT-A-HOST.md` (per-host stanzas, wire flags, http exposure, doctor
schema table, troubleshooting — passes the docs police); README "Drop it into any
project" section + docs-table row; PLAN §6 shipped-slice annotation; ADR-0015
("onboarding is an operator command, never an agent tool": a wire writes outside the
sandbox by definition and reads other apps' config blocks, so it must never be a
capability the agent holds).

## 3. What is NOT done (P6 rest, in order)

1. **PR + merge** this branch into `main` (house flow: `gh pr create`, then `--merge`).
2. **Tagged releases**: wheels + sdist built in CI, GitHub release page, `pipx` story in
   README/CONNECT. Version is still `0.1.0`; semver the bump (`0.2.0` fits — no envelope
   change, new CLI surface). sdist include-list already carries skills/docs/config.
3. **Security pass**: `pip-audit` on the extras only; property tests for the sentinel
   parser and path normalization; the red-team bypass matrix (`..`, absolute-external,
   symlink escape, device names, `\\?\`, env injection, CLIXML spoofing, spill-path
   traversal) as executable tests.
4. **Windows CI runner** (needs the App `workflows` permission; same blocker as
   `ci.yml` itself). Turns `@pytest.mark.win` skips into real checks. New surfaces that
   will meet Windows for the first time: wire's `%APPDATA%` paths (unit-tested via the
   `env` parameter only), doctor's thread-based probe, http transport.
5. Small honesty wins still open from last session: publish task in `tests/eval/suite.jsonl`
   + replay fixture; store `expiry`; `registry.explain_all`.

## 4. Next steps (in order)

1. Open the PR for this branch, confirm mergeable, merge (`--merge`, keep branch), pull
   `main` into the next session's branch.
2. Land `ci.yml` (still permission-blocked; the local repro is `ruff check . && pytest -q`).
3. Packaging: `python -m build`, install the wheel in a fresh venv, `sk wire` +
   `sk doctor` from it (the full loop works from an editable install today; prove it
   from a wheel), then tag + release.
4. Security pass, then Windows CI (both P6; acceptance criteria in PLAN §6).

## 5. How things run here (operational)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # sandbox recycles wipe .venv
.venv/bin/pytest tests/ -q                       # 733 passed, 3 skipped, 1 xfailed, ~57s
.venv/bin/ruff check .                           # clean
.venv/bin/sk doctor                              # ok: true, live stdio probe ~1.5s
.venv/bin/sk wire --project                      # writes .mcp.json etc. in cwd
.venv/bin/python -m skeletonkey.mcp --transport streamable-http --port 8765
```

`sk` global flags (`--root`, `--json`, `--read-only`, `--cwd`) go **before** the
subcommand (argparse aborts otherwise) — `sk wire` subcommand flags (`--project`,
`--check`, `--remove`…) go after it. Sandbox recycles: `.venv` and `.git` reset
mid-session — recreate the venv, fetch the branch tip, `git reset --mixed` to it; push
early, the remote is the only durable record.

## 6. Ideas (honest, none decided)

- `sk wire --all --yes` non-interactive mode for provisioning scripts (today every run
  is already non-interactive; a batch "wire + doctor + report" one-shot could compose
  the two instead of adding a third command).
- Doctor as a **tool** was rejected (ADR-0015) but a *sanitized* projection (no host
  configs, no paths outside roots) could still let the autopilot ask "is my engine
  healthy?" — `registry.stats` may already cover most of it; check before building.
- `sk export openai-tools` (P7 bridging): the manifest is the single definition; same
  `wire`-style operator command shape would fit.
- The http transport has no auth by design (loopback default); an operator-grade
  reverse-proxy example (caddy/nginx block) would fit CONNECT-A-HOST.md.

## 7. Standing constraints (carried, reaffirmed)

- **Licensing/identity frozen:** Apache-2.0, authorship "Dime", README title + tagline
  unchanged. No relicense/retitle/author-tidying without the owner.
- **Python 3.11+, zero mandatory deps** (ADR-0001) — now *enforced* by
  `tests/test_core_purity.py`; the onboarding commands (`sk wire`, `sk doctor
  --no-probe`) must stay stdlib-only too.
- **Windows + Linux + macOS first-class; PowerShell not optional.** New code paths use
  threads not `select` (doctor probe), `env`-parameterized path resolution (wire tests
  never touch a real home), and rendered-payload assertions for pwsh.
- **Primary consumer is the bespoke autopilot loop; MCP surface ships and stays honest.**
  No silent reordering; a remote server's error code is never re-wrapped; wire/doctor
  reports are data with a versioned schema (`sk.wire/1`, `sk.doctor/1`).
- **House rule for new tools:** TOOL-CONTRACT section, skill-guidance entry, wire-level
  test. `sk wire`/`sk doctor` are CLI subcommands, deliberately NOT registry tools
  (ADR-0015) — the BURST table and TOOL-CONTRACT were correctly not touched. Spec-first
  + 3-ish chunks + explicit `git add`; `.github/` untracked.

## 8. Landmines (measured this session; old ones still bite)

- **mcp 2.1.1 client result models are snake_case**: `init.server_info`,
  `res.is_error`, `res.structured_content` (the handoff's camel-case warning applies to
  the *client* side too now). The http client factory is `streamable_http_client`
  (renamed from `streamablehttp_client`); `client.py` accepts both.
- **uvicorn cannot nest in asyncio.run** — that was the streamable-http crash; keep
  `serve_http` outside the loop. If you ever add an async HTTP server path, same rule.
- **streamable-http endpoint path is `/mcp`** (SDK default) — the banner, the url
  stanza and the test all assume it.
- **The suite hangs if `watch.available()` is real and watchfiles is installed** — the
  watcher test is pinned now; do not un-pin it without adding a `stop_after`.
- **`sk doctor` state semantics:** fresh (never-ran) workspace is *healthy*; partial
  state (state dir exists, spill/journal missing) is the failure. Don't "fix" the fresh
  case — the wire→doctor first-run flow depends on it.
- **`sk wire` must never write a real user config from tests** — every test goes through
  the `env` parameter; project mode skips hosts without project paths precisely so a
  `--project` run cannot fall back to `$HOME`. Keep it that way.
- **pyc staleness in-session** (hit again this session): after editing a module, a
  same-second import can serve stale bytecode — clear `__pycache__` and retry.
- `SkeletonKeyError.err.code` is the string `"BAD_ARGS"`; `registry.all()` is a method;
  `AdSnapshot` has `.tokens`/`.digest`; remote tests need the package editable;
  doctor's scratch build re-probes the profile each run (fine, ~0.5s).
- Old unchanged: `E` namespace class; `_ledger` swallows exceptions; legacy
  `deny: ["**"]`; path denies need `tool(**/glob)`; `fs.glob` dotfile behavior;
  `ReadResult.sha256` 16 chars; `req.meta` plain dict + positional progress args;
  replay task_id match; `cmd | head` exit code; pytest from repo root; lowlevel
  `tools/list` params model is `PaginatedRequestParams`; result `meta` serializes as
  `_meta`; `test_policy_property.py` BURST table must name every mutating tool;
  `test_docs.py` is the docs police (CONFIG_RE has **no** file-extension escape — a
  backticked `mcp.json` fails; write such filenames without backticks);
  `examples/live_hmr/orbital.py` mirrors `skeletonkey/live/demos.py`; skill inject cap
  for fs-safe-refactor ≈ 3995 tokens.

## 9. Where things live (pointers, not contents)

- `skeletonkey/wire.py` — host catalogue (`HostSpec`, `hosts()`), merge engine
  (`wire()`), read-only scan (`status_rows()`); JSONC stripping in `_strip_jsonc`.
- `skeletonkey/diagnostics.py` — `doctor()` (fixed check order, scratch toolkit),
  `probe_stdio()` (thread-pumped JSON-RPC, `_ProbeFailure` carries stderr tails).
- `skeletonkey/mcp/__main__.py` — `amain` (stdio) vs `serve_http` (loop ownership).
- Tests: `test_wire.py` (19), `test_doctor.py` (10), `test_mcp_http.py` (1, real SDK
  client), `test_core_purity.py` (2, `-S` subprocess).
- Docs: `docs/CONNECT-A-HOST.md`; `docs/adr/0015-onboarding-is-an-operator-command.md`;
  PLAN §6 (shipped-slice annotation) + §9 ADR index row 0015.
- `tests/test_skill_synthesis.py::test_watcher_reports_why_it_cannot_run…` — the pinned
  watcher test (see landmines).
