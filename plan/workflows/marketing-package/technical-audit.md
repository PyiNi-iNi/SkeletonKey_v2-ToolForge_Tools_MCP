# Marketing package technical audit

**Scope:** live preview, visualizer/catalog, replay evidence, publishing helpers, and the
missing player/nester/upload lifecycle.
**Date:** 2026-09-20
**Method:** source inspection, `git grep`, compile check, CLI smoke check, and isolated manual
HTTP probes. This is an engineering audit, not a claim that the full pytest suite ran.

## A. Verified inventory

### Search result

The repository contains no registered or implemented surface for:

- HTML5 video playback, MP4/WebM/Ogg serving, byte ranges, media duration, or seek state;
- screen/window capture, audio capture, FFmpeg/media encoding, editing, thumbnails, or
  captions/subtitle generation;
- browser automation, extension/user-script control, iframe injection, domain mutation, or
  website ownership verification;
- upload sessions, OAuth/provider asset ids, CDN/object storage, visibility state, or delete
  callbacks.

The only recording subsystem is `skeletonkey/core/replay.py:RunRecorder`, which stores JSONL
engine calls and full result envelopes plus a baseline workspace for `sk replay`. It is not a
screen/audio recorder and is not automatically attached to ordinary `Engine.call` traffic.

### Existing HTTP surfaces

| Surface | Mutating routes | Read routes | Structural posture |
| --- | --- | --- | --- |
| `Panel` (`skeletonkey/live/panel.py`) | `POST /repl`; `POST /api/control` (`reload`, `stop`, `start`, `patch`, `render`, snapshot save/restore) | `/`, `/view3d`, `/agents`, `/version`, `/frame.svg`, `/scene.json`, `/state`, `/api/program`, `/api/history`, `/api/activity`, `/events` | Executable panel; never public by default. |
| `VizServer` (`skeletonkey/viz/server.py`) | No `do_POST`/`do_PUT`/`do_DELETE`/`do_PATCH` | Catalog page, model, digest, explain, runtime, tool, health | Structurally read-only, but unauthenticated and CORS-open. |
| `VizMount` (`skeletonkey/viz/mount.py`) | Refuses non-`GET`/`HEAD` in `dispatch`; host also fences `POST` | Catalog routes under `/tools` | Read-only projection inside an executable Panel; weaker than standalone server. |

The distinction is important: “can I show a page?” is implemented; “can I safely publish a
player or inject it into a domain?” is not.

## B. Reproduced findings

The following probe was run against a temporary workspace with `panel_repl=false` and a live
program. It did not touch the repository source tree.

```text
live.start: ok=True
live.serve(host="0.0.0.0", port=0): url=http://127.0.0.1:<ephemeral>/
POST /api/control {"action":"render"}: HTTP 200
GET /api/activity: {"engine": []}
frame listeners before restart: 1
frame listeners after stop/start: 2
live.max_source_bytes=10 with ~1 KiB source: live.start ok=True
```

These observations map directly to D-02 through D-06 in the parent plan. They should become
regression tests before any remote recording instructions are published.

## C. Defect register with evidence and fixes

### D-01 — public panel has no authenticated mutation boundary (P0)

**Evidence**

- `Panel._send()` always emits `Access-Control-Allow-Origin: *` (`skeletonkey/live/panel.py`).
- `Panel.do_POST()` accepts `/repl` and `/api/control` with no token, origin allowlist, or CSRF
  check.
- `live.serve` and `sk dev` accept an arbitrary bind host; CLI help explicitly recommends
  `0.0.0.0` in preview sandboxes.
- `VizServer` also emits wildcard CORS, although its routes are read-only.

**Impact**

A reachable panel exposes program state and code/state mutation. Same-origin policy is not an
authentication mechanism. Even if JSON CORS preflight fails, a cross-origin form or `no-cors`
request can still reach a simple POST endpoint; the endpoint itself performs no origin check.

**Fix options**

1. Safest default: reject non-loopback binds unless an explicit `--unsafe-public-panel`/config
   switch is set; show a typed refusal and a remediation message.
2. If public preview is required: issue a high-entropy, expiring bearer token out-of-band,
   require it on every read/write route as appropriate, validate `Origin`/`Host`, implement
   CSRF protection for cookie/browser flows, rate-limit requests, and remove wildcard CORS.
3. Split a static/read-only player/catalog server from the live control server. Do not make the
   player a reverse proxy to the panel.

**Tests**

- public bind without explicit opt-in fails;
- missing/expired/wrong token gets 401/403 on every control route;
- disallowed `Origin` is rejected;
- allowed read-only player has no mutating handler;
- no secret appears in URLs or access logs.

### D-02 — `panel_repl=false` does not produce a read-only panel (P0)

**Evidence**

`Panel.do_POST()` routes `/api/control` before checking `panel.repl_enabled`. The flag only
checks `/repl`. The existing test `tests/test_live.py::TestPanel::test_panel_honours_repl_off`
checks only `/repl`, while ADR-0018 already acknowledges that `/api/control` still mutates.

**Reproduction**

With `panel_repl=false`, `POST /api/control {"action":"render"}` returned HTTP 200 and
mutated the frame. `start`, `stop`, `patch`, `reload`, and snapshot actions are also in the
control switch.

**Fix**

Rename the flag to reflect its scope, or enforce it before both write surfaces. Prefer separate
flags such as `panel_control=false` and `panel_repl=false`, with `panel_control=false` the
safe default for any non-loopback exposure. Add one test per control action and a traversal test
for the mounted prefix.

### D-03 — public/proxy URL is not browser-correct (P1)

**Evidence**

`Panel.url` and `VizServer.url` replace `0.0.0.0`, empty host, and `::` with
`127.0.0.1`. This is useful as a local display convention but wrong when the browser is outside
the Python process or behind a port proxy.

**Impact**

A remote operator is instructed to open a loopback URL that points at their own machine. A
recording harness may capture a dead page while the server is healthy.

**Fix**

Separate bind address from advertised origin. Add an explicit `public_base_url`/proxy-origin
configuration; otherwise return a relative URL or a structured `{bind, advertised_url,
proxy_required}` result. Test `0.0.0.0`, IPv6, forwarded host/port, and the Arena-style proxy.
Browser code must use same-origin/relative API paths, not hard-coded localhost.

### D-04 — panel activity feed reads the wrong Engine attribute (P1)

**Evidence**

`Panel._engine_activity()` reads `getattr(eng, "_ledger", None)` at
`skeletonkey/live/panel.py`, while `Engine.__init__` stores the ledger as `self.ledger` in
`skeletonkey/core/engine.py`. The activity route therefore returns an empty list even after
successful calls. The current test asserts only that the result is a list, so it passes while
the feature is broken.

**Fix**

Use the public `engine.ledger` attribute (or a deliberate accessor), add a test that makes a
call before reading `/api/activity`, and redact path/argument data before presenting it in a
marketing frame.

### D-05 — frame-listener leak on panel restart (P1)

**Evidence**

`LiveManager.add_frame_listener()` only appends. `Panel.start()` adds `self.bump`;
`Panel.stop()` never removes it. Repeated `live.serve` stop/start leaves old `Panel` objects
reachable through the manager listener list.

**Impact**

Memory retention and O(number-of-restarts) callback fan-out. Stale condition variables are
not visible in a single take, but a long-lived recording/rehearsal session accumulates them.

**Fix**

Add identity-based `remove_frame_listener()`, remove in `Panel.stop()` (including failed start
paths), and make registration idempotent. Test 20 start/stop cycles with exactly one listener
and no callbacks after stop.

### D-06 — configured source-size limit is dead (P1)

**Evidence**

`LiveConfig.max_source_bytes` exists at `skeletonkey/core/config.py:224`, but source reads in
`LiveProgram._read_source()` and dependency reads do not consult it. A manual run with the
limit set to 10 accepted approximately 1 KiB of Python.

**Fix**

Check byte size before decoding and before every initial/reload/dependency exec. Return a typed
`TOO_LARGE` error with path, observed bytes, configured limit, and no namespace mutation. Add
initial-load, reload, dependency, and panel-start tests.

### D-07 — `/api/control` request body is unbounded (P1)

**Evidence**

`POST /repl` rejects bodies over 64 KiB, but `_control()` reads `Content-Length` without a
maximum. A reachable panel can be made to allocate an arbitrary declared body before JSON
parsing.

**Fix**

Apply a small endpoint-specific cap before reading, reject missing/negative/invalid lengths,
handle chunked requests explicitly (or reject them), and test 413 behavior. Enforce total
request rate and concurrent control limits for public mode.

### D-08 — publish injection is not all-or-nothing under write failure (P1)

**Evidence**

`pub_inject()` does a complete read/plan pass, then calls `fs.write()` sequentially for each
changed file. `expect_sha` protects each file, but a concurrent edit or I/O error after file 1
writes can leave file 1 changed while file 2 and later files are untouched.

**Fix options**

- Add a journal transaction/batch commit with rollback on any write failure;
- or preflight all current hashes immediately before commit and provide a compensating rollback
  receipt if commit fails;
- test injected failure on file 2 and assert the workspace returns to its exact pre-call tree.

The existing missing-marker refusal is still valuable, but it is not a general transaction.

### D-09 — replay evidence is sensitive and not automatically produced (P1)

**Evidence**

`RunRecorder` writes full result envelopes and copies a workspace baseline. The normal toolkit
path does not automatically create a marketing recording; CLI `sk replay` consumes a recording
but does not create one. Full result data can include paths, file content, URLs, or other
sensitive material even when the ledger preview is redacted.

**Fix**

Add a dedicated marketing harness rather than silently changing core replay semantics. It should
use a clean fixture, redact/normalize before export, hash the source and toolset, mark the
recording sensitive until review, and never copy the operator's real workspace. Add a manifest
schema and a linter for secret/path leakage.

### D-10 — media lifecycle is absent (P1 product gap)

There is no media asset type, player route, recorder bridge, caption pipeline, upload adapter,
provider id, visibility state, or revoke/delete action. The plan must keep this gap visible and
not infer capability from `live.serve` or `multimedia_gen`.

### D-11 — HTTP method/preflight semantics are incomplete (P2)

- `Panel` has no `do_HEAD` or `do_OPTIONS`.
- `VizMount` lists `HEAD` as safe but `Panel.do_GET()` always dispatches `"GET"`, and the
  mount returns a body for direct `HEAD` dispatch.
- `VizServer.do_HEAD()` calls `do_GET()`, so it writes a response body for a HEAD request.
- No CORS preflight route exists.

Fix with a shared response helper that suppresses bodies for HEAD, explicit OPTIONS behavior,
strict content types, and tests for range/HEAD/preflight once a player exists.

### D-12 — standalone runtime catalog is too open for some public contexts (P2)

`VizServer` is read-only but wildcard CORS allows any origin to read `/api/runtime`, which can
include ledger-derived tool timings and metadata. A static catalog is safer for public sharing.
Either default to no CORS, allowlist configured origins, and disable runtime overlays by default
for public mode, or document the metadata as intentionally public and keep paths redacted.

### D-13 — HMR dependency scope is deliberately incomplete (P2 gap)

`docs/LIVE-IMPL-PLAN.md` marks cross-package dependency closure as “next.” The current resolver
tracks same-tree imports, not arbitrary package/import graphs. A showcase that edits a library
outside the program tree can appear not to update. Keep demos same-tree or add the closure
before promising general live dependency refresh.

## D. Focused regression suite to add

| Test file | New assertions |
| --- | --- |
| `tests/test_live.py` | `panel_repl=false` blocks `/api/control`; source-size cap; control body cap; listener removal; activity row; public bind policy; advertised URL. |
| `tests/test_viewport_link.py` | proxy/base URL separation; no localhost in browser-facing advertised URL; link scheme/origin policy. |
| `tests/test_viz_mount.py` / `tests/test_viz_server.py` | true HEAD semantics, OPTIONS policy, CORS policy, runtime overlay redaction. |
| `tests/test_publish.py` | file-2 write failure rolls back file 1; no secret in result/ledger/sidecar. |
| New `tests/test_marketing_manifest.py` | schema, hash verification, required tool sequence, source path containment, secret/path scanner. |
| New `tests/test_media_player.py` (future) | range/HEAD/TTL/revoke/isolation; no engine or live manager handle. |
| New `tests/test_media_embed.py` (future) | allowlisted origin, dry-run, explicit approval, nonce/postMessage validation, undo/revoke. |

## E. Acceptance commands

From a clean checkout with dependencies installed:

```bash
python -m compileall -q skeletonkey features
ruff check .
pytest -q tests/test_live.py tests/test_viewport_link.py tests/test_viz_mount.py \
          tests/test_viz_server.py tests/test_publish.py
bash scripts/ci_all_gates.sh
python -m skeletonkey.cli --cwd <fresh-workspace> doctor
python -m skeletonkey.cli --cwd <fresh-workspace> viz --summary
```

The final marketing gate additionally runs the fixture harness, validates every sidecar,
replays every tool trace in a scratch copy, scans all media/metadata text for secrets and
private paths, and verifies that the uploaded provider asset hash matches the approved master.
