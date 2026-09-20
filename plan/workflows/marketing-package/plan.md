# Marketing package: engineering plan for tool showcases, recordings, and uploads

**Workspace:** `plan`
**Workflow:** `workflows`
**Package:** `marketing package`
**Status:** local-only planning; public/remote preview is blocked by findings in the defect register
**Audit date:** 2026-09-20
**Companion doc:** [`scripting-is-fun.md`](scripting-is-fun.md)
**Technical appendix:** [`technical-audit.md`](technical-audit.md)

## 1. Decision summary

The repository has strong material for **live product demonstrations**, but it does not have
a video player, screen recorder, media library, uploader, browser automation layer, or
arbitrary-domain nester. The marketing package must therefore separate four systems:

```text
SkeletonKey tools  ->  live HTML/SVG preview  ->  external screen recorder
        |                      |                         |
        +-- ledger/replay -----+                         +-- external upload host
```

Use the current project to demonstrate real tool behavior and use an external recorder and
upload provider for audiovisual assets. Do not describe the live panel, `VizServer`, or
`VizMount` as a video player or public embed system.

### Current capability decision

| Need | Current answer | Decision |
| --- | --- | --- |
| Show a running demo | `live.start`, `live.serve`, `live.render`, `live.scene`, `sk dev` | Use for private/local captures after the security fixes below. |
| Show the catalog | `sk viz --serve`, `sk export viz-html`, or private `sk dev` `/tools` mount | Use standalone or static export when the audience must not receive code execution. |
| Prove tool calls | Engine ledger plus optional `RunRecorder`/`sk replay` | Add a sidecar and replay fixture; neither is an audiovisual recording. |
| Record screen/audio | No repository implementation | Use an external recorder; record its settings in the sidecar. |
| Play a finished video | No repository implementation | Future `media.player.*` work, separate from `live.*`. |
| Temporarily nest/embed a player | No repository implementation; `VizMount` only mounts its own catalog | Future allowlisted iframe/browser-companion work; never arbitrary silent domain injection. |
| Upload a video | `pub.*` provides publishing knowledge, packaging, testing, and secret-safe file injection only | Use an external provider or design a future optional upload adapter. |

## 2. Objectives, non-goals, and release criteria

### Objectives

1. Pair every recorded claim with a real tool id, source fixture, exact invocation, and
   observable acceptance result.
2. Produce deterministic clips that another engineer can rerun from a commit and a clean
   workspace.
3. Keep code-execution surfaces private and keep credentials out of frames, transcripts,
   captions, thumbnails, manifests, and provider URLs.
4. Make upload/review state auditable without pretending the repository owns the upload.
5. Turn defects found during rehearsal into explicit engineering work instead of editing the
   narration to hide them.

### Non-goals for this package

- Building a general screen recorder, video editor, CDN, or social uploader in the core.
- Fetching arbitrary remote media or proxying arbitrary websites.
- Injecting an iframe/script into a third-party domain without an explicit user-controlled
  browser surface and origin policy.
- Calling a generated SVG frame a video, animation, or seekable playback asset.
- Recording real credentials, payment accounts, private domains, or unreleased source code.

### Definition of done

- [ ] Defect gates D-01 and D-02 are fixed and covered before any non-loopback preview is
      used. D-03 through D-07 are fixed or explicitly accepted for a local-only rehearsal.
- [ ] Each showcase row has a fixture hash, source commit, exact tool sequence, expected
      result fields, narration, capture settings, and cleanup procedure.
- [ ] A fresh environment passes compile, lint, focused tests, and the applicable `sk doctor`
      checks. Missing optional MCP/watch dependencies are recorded as environment facts, not
      silently presented as product support.
- [ ] Each clip has a `marketing.showcase/1` sidecar, SHA-256 checksum, captions, poster, and
      review status.
- [ ] A clean-browser review confirms that no secret, private path, local-only URL, or false
      capability claim escaped.
- [ ] Upload provider, asset id, visibility, checksum, reviewer, and revoke/delete procedure
      are recorded. No upload is called complete because a local file exists.

## 3. System map and trust boundaries

### Existing execution path

```text
engine.call(tool, args)
  -> registry gate/validation/policy/budget
  -> handler
  -> envelope + ledger/reliability record
  -> optional RunRecorder step

live.start
  -> LiveManager.resolve (workspace sandbox)
  -> LiveProgram.load_initial (in-process Python execution)
  -> optional FileWatcher

live.serve / sk dev
  -> Panel HTTP server
  -> browser GET /frame.svg, /scene.json, /state, /version, /events
  -> browser POST /repl or /api/control (code/state mutation)
  -> optional VizMount GET /tools/* on the same bind
```

The panel is **not a sandbox**. `live.start`, `live.repl`, `live.patch`, reload hooks, panel
`/repl`, and panel `/api/control` execute or mutate in-process state. The filesystem sandbox
limits path resolution, but it does not turn arbitrary Python into a safe public web endpoint.

### Exposure modes

| Mode | Surface | Trust level | Marketing use |
| --- | --- | --- | --- |
| Loopback | `live.serve` at `127.0.0.1` | Local operator controls the browser and process | Preferred for recording. |
| Merged dev viewport | `sk dev` / `mount_tools=true` | `/tools` shares a bind with executable panel routes | Private rehearsal only; never market as public read-only embed. |
| Two servers | `sk dev --two-servers` | Standalone catalog has structural read-only handler; panel remains executable | Good private comparison; expose only the catalog if panel stays private. |
| Static catalog | `sk export viz-html -o tools.html` | No live server or write endpoint | Preferred shareable catalog artifact. |
| Preview proxy / `0.0.0.0` | Publicly reachable bind depends on host/proxy | Currently unsafe without auth/origin fixes and URL/proxy fixes | Blocked by D-01–D-03. |

## 4. Repository inventory and player/nester deep dive

### What exists

| Surface | Files | Useful behavior | Boundary |
| --- | --- | --- | --- |
| Live HMR | `skeletonkey/live/runtime.py`, `skeletonkey/live/tools.py`, `skeletonkey/live/panel.py`, `docs/LIVE-HMR.md` | Python file watching, transactional scratch exec, in-place function/class patching, 3-way state merge, SVG/JSON scene output, 3D page, REPL/debugger | In-process arbitrary Python; no media file or playback timeline. |
| Canned visual demo | `examples/live_hmr/orbital.py`, `skeletonkey/live/demos.py` | 2D orbit, HUD, 3D mesh/cube, state counter, handler registry | A live render fixture; it is not a video asset. |
| Tool visualizer | `skeletonkey/viz/server.py`, `skeletonkey/viz/mount.py`, `skeletonkey/viz/render.py` | HTML catalog, graph, gates, runtime overlay, `/api/*`, health route | `VizMount` only projects the SkeletonKey registry under its host prefix. |
| Safe file/policy surface | `fs.*`, `policy.*`, `skeletonkey/fsx/`, `docs/SECURITY-MODEL.md` | Search/read/patch/write, journal, undo, policy receipts | A mutation can still be real; use disposable fixtures. |
| Reproducibility | `skeletonkey/core/replay.py`, `docs/adr/0009-replay-proves-the-turn.md` | Full tool envelopes in JSONL, baseline copy, scratch replay and diff | It records tool execution, not pixels/audio; full envelopes need artifact hygiene. |
| Publishing support | `pub.*`, `skeletonkey/core/publish.py`, `skeletonkey/publish_data.py` | Platform/packaging/test-plan knowledge, write-only credential metadata, placeholder scan/injection | No HTTP upload, asset store, OAuth client, transcoder, captioner, or player. |
| Optional multimedia | `features/multimedia_gen/{image_gen,stt,tts}` | Image generation, speech-to-text, text-to-speech, streaming test backends | No video decode/encode/playback/recording. |

### Search conclusion

No implementation or registered tool was found for `<video>`, MP4/WebM playback, range
serving, screen capture, FFmpeg/media encoding, iframe injection, browser extension control,
website mutation, upload sessions, or video asset lifecycle. The only “recording” terminology
is the developer-facing `RunRecorder`, which stores JSONL tool envelopes and a workspace
baseline.

### Current nearest matches are not substitutes

- `live.serve` returns an HTTP preview URL and serves SVG/scene/state routes. It can open a
  browser page locally but cannot play a finished recording, seek, buffer, caption, or upload.
- `VizServer` is structurally read-only, but only for tool metadata. `panel_url`/`viz_url` are
  escaped links; neither side fetches, frames, or proxies the other.
- `VizMount` is a private route projection at `/tools`. It is not a “nester” for another
  website or domain, and its merged host inherits the panel's executable origin.
- `features/multimedia_gen` produces/decodes audio/image test data, not a video pipeline.
- `pub.inject` writes stored values into workspace files. It is not an upload operation and
  must not be used to put a credential into a recording URL.

## 5. Showcase catalogue and exact alignment

The clips below are ordered by engineering value. Each row is a testable claim, not just a
marketing theme. The expected result is the minimum evidence that must appear in the screen
capture or sidecar.

| ID / duration | Claim | Fixture/source | Tool/command sequence | Acceptance evidence | Blocker |
| --- | --- | --- | --- | --- | --- |
| `00-catalog` / 30–45s | The host advertises a bounded, explainable toolset. | Current registry; `docs/TOOLSET.txt`. | `profile.probe` → `registry.list` → `registry.describe` → `sk viz --summary`; optional `sk export viz-html`. | Host/profile receipt, registered vs advertised count, digest, one gated-tool explanation, static HTML opens offline. | `sk doctor`/toolset setup must be clean. |
| `01-discover` / 45–60s | Natural-language routing finds a usable capability instead of guessing an id. | A harmless task such as “find occurrences and undo an edit.” | `registry.search` or `registry.route` → `registry.describe` → `planner.plan`. | Ranked result, risk/tier/schema, replayable args, and plan conflict/warning fields. | Do not claim semantic routing unless the configured backend is actually enabled. |
| `02-safe-edit` / 60–90s | A real edit produces a receipt and can be reverted. | Disposable fixture copied into a clean workspace. | `fs.search` → `fs.read` → `fs.patch` → `fs.journal_list` → `fs.undo` or `fs.undo_task`. | Before/after digest, unified diff, `undo_token`, journal row, restored bytes/digest. | Never use a real source tree or credential-bearing file. |
| `03-live-hmr` / 75–120s | A running Python program changes without restart and preserves runtime state. | `examples/live_hmr/orbital.py` / `skeletonkey/live/demos.py`; `docs/LIVE-HMR.md`. | `live.start` → `live.serve` → `live.status` → `live.repl` (`hue`/camera) → `fs.patch` or editor save → `live.status`/`live.render`. | Frame version advances, counter/state survives, reload report is `ok`, new SVG contains the change, broken-save overlay behavior is shown only if intentionally tested. | Requires private panel; do not expose `/repl` during capture. |
| `04-viewport` / 60–90s | The running scene and tool contract can be inspected together. | Orbital demo plus `skeletonkey/viz/mount.py`; ADR-0017/0018. | Private `sk dev app.py` → `/` → `/tools` → tool detail/gate; compare with `sk viz --serve` or `sk export viz-html`. | `/tools` model/digest/health, one tool explanation, explicit statement that merged mode is executable. | Public sharing uses standalone/static catalog, not merged `/tools`. |
| `05-integrations` / 60–90s | Integration discovery and health are explicit and honest about optional dependencies. | `skeletonkey/integrations/examples/simple_demo.py`; `docs/INTEGRATION-DEVELOPMENT.md`. | `integrations.discover` → `integrations.list` → `integrations.describe` → `integrations.validate` → `integrations.health` → `integrations.load`. | Manifest, capabilities, validation result, healthy zero-dependency example, and a real missing-dependency result if demonstrated. | Do not turn a skipped optional backend into a green success. |
| `06-publish-prep` / 60–90s | Release preparation can be planned without putting secrets in the demo. | Fake template containing `{{PUB.demo.token}}`; publishing manifests. | `pub.platforms` → `pub.packaging` → `pub.testers` → `pub.placeholders` → `pub.inject {dry_run:true}`. | Target/packaging steps, marker locations, dry-run changed-file list, missing/expired refusal, metadata-only store listing. | No real `pub.store_put` or actual upload in a recorded take. |
| `07-multimedia-support` / optional 60s | The optional feature area can generate or transcribe supporting media. | `features/multimedia_gen/` README/tests. | Feature API/CLI as documented, with backend receipt. | Clearly labeled image/STT/TTS output and fallback/backend status. | Never call it video playback or recording. |

### Per-showcase manifest

Every row must produce a machine-readable sidecar. Proposed shape:

```json
{
  "schema": "marketing.showcase/1",
  "id": "03-live-hmr",
  "source": {
    "commit": "<40-char-sha>",
    "dirty": false,
    "demo_path": "examples/live_hmr/orbital.py",
    "demo_sha256": "<sha256>",
    "python": "3.11.x",
    "os": "linux|windows|darwin",
    "toolset_digest": "<sk-viz-digest>",
    "config_digest": "<redacted-config-hash>"
  },
  "execution": {
    "command": "<redacted command>",
    "tool_calls": ["live.start", "live.serve", "live.repl", "live.render"],
    "run_recording": "<jsonl path or null>",
    "ledger_digest": "<digest or null>"
  },
  "media": {
    "master": "<path or provider object>",
    "duration_s": 92.4,
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "codec": "<recorder output>",
    "captions": "<vtt path>",
    "poster": "<png path>",
    "sha256": "<master sha256>"
  },
  "distribution": {
    "provider": "<external provider>",
    "asset_id": "<provider id>",
    "visibility": "draft|unlisted|public",
    "url": "<reviewed URL>",
    "uploaded_at": "<UTC timestamp>"
  },
  "review": {"status": "pending|approved|rejected", "reviewer": "<id>", "notes": []}
}
```

Do not store raw credentials, signed upload URLs, bearer tokens, or full secret-bearing
recordings in this sidecar. The `RunRecorder` JSONL is a test artifact; treat it as sensitive
until normalized and reviewed.

## 6. Recording and upload runbook

### Phase R0 — environment and preflight

1. Create a disposable workspace and state directory outside the repository's tracked files.
2. Install the required test/runtime profile explicitly:
   `python -m pip install -e '.[dev,mcp]'` for the full local gate; add `[watch]` only when
   testing the accelerated watcher.
3. Run `python -m compileall -q skeletonkey features`, `ruff check .`, the focused test set,
   and the applicable full suite. Use `scripts/ci_all_gates.sh` in CI.
4. Run `python -m skeletonkey.cli --cwd <workspace> doctor` from a fresh state directory.
   A missing MCP extra or a reused partial state directory is an environment failure and must
   be recorded/fixed before a “healthy host” claim.
5. Record commit, dirty status, Python/OS, config/profile receipts, and `viz --summary` digest.
6. Rehearse the exact sequence with fake data. Verify stop, undo, snapshot restore, and
   cleanup before starting the recorder.

### Phase R1 — capture

- Use loopback panel mode for `03-live-hmr` and `04-viewport`; use the standalone/static
  catalog for any artifact that leaves the operator's machine.
- Put the tool call or CLI output immediately before the visible result. Hold result states
  for at least two seconds; capture errors only when they are part of the claim.
- Capture terminal and browser separately when possible; do not rely on a URL printed as
  `127.0.0.1` being reachable from a remote browser (D-03).
- Record audio/narration separately from screen capture so an engineering correction does not
  require a new screen take.
- Keep a take number. Never overwrite a rejected master; mark it rejected in the sidecar.

### Phase R2 — evidence and review

1. Close the live server and confirm the port is released.
2. Normalize the tool run recording: remove volatile paths/timestamps, scan for secrets, and
   retain only the minimum envelopes needed to reproduce the claim.
3. Verify sidecar hashes against the final master, captions, poster, and run recording.
4. Clean-browser review: captions, title, URLs, source paths, error claims, feature flags,
   fallback behavior, and public/private boundary.
5. Reviewer signs off the sidecar. Visibility remains `draft` or `unlisted` until this step.

### Phase R3 — external upload

- Select a provider manually or through a future optional adapter; use `pub.platforms` and
  `pub.packaging` for release research, not as an upload claim.
- Upload the exact hashed master. Record provider asset id, URL, visibility, timestamp, and
  checksum. Do not paste a provider token into a workspace file or URL.
- Re-open the asset from a clean browser/profile, verify playback/captions/poster, then promote
  visibility. Record the rollback/delete operation.
- Delete/revoke test uploads, stop the panel, remove the disposable workspace, and rotate any
  temporary credentials.

## 7. Future player/nester/upload design (not implemented)

Do not add this behavior to `live.serve`. Create a separate, optional media subsystem with a
read-only player origin and an explicit control plane.

### Proposed interfaces

| Proposed id | Role | Minimum contract |
| --- | --- | --- |
| `media.player.prepare` | Validate a local/approved remote asset and create a player manifest | Scheme/host allowlist, MIME/duration/size checks, captions/poster, checksum; no fetch of arbitrary URLs. |
| `media.player.serve` | Start an isolated static player server | GET/HEAD and byte ranges, correct `Content-Length`/`Content-Range`/`ETag`, signed expiring URL, loopback by default. |
| `media.player.revoke` | Stop a session and invalidate URL | Idempotent revoke, expiry receipt, no stale cache after revoke. |
| `media.embed.plan` | Produce an iframe/snippet without changing a domain | Dry-run target origin, CSP/frame policy, nonce, TTL, `postMessage` origin; no arbitrary HTML mutation. |
| `media.embed.apply` | User-approved browser-side insertion | Explicit user gesture or extension permission, allowlisted origin, visible marker, undo/revoke. |
| `media.upload` | Optional provider adapter | OAuth/token outside workspace, resumable/idempotent upload, checksum, visibility, provider id, delete/retry. |

### Player security/HTTP requirements

- Separate process/object graph from the executable live panel; no engine, REPL, shell, or
  workspace write handle.
- Loopback default; public mode is explicit, authenticated, rate-limited, and emits a
  non-secret URL. Do not use a bearer token in a query string without expiry/revocation and
  redacted access logs.
- Allow only approved local files or `https` hosts; prevent path traversal, SSRF, symlink
  escape, and MIME confusion. Never proxy a third-party website to “make embedding work.”
- Implement `GET`, correct `HEAD`, byte-range seeking, bounded request sizes, `X-Content-Type-
  Options`, restrictive CSP, `frame-ancestors`, and a clear `Content-Security-Policy` for the
  player page.
- TTL and explicit revoke must invalidate the route and any signed media URL. Test process
  teardown, expiry, restart, concurrent range requests, and cache behavior.

### Nester/browser requirements

A server cannot silently inject into an arbitrary remote domain because of browser same-origin
policy and the target's CSP. Choose one explicit model:

1. Generate a self-contained iframe snippet and let the site owner paste it; or
2. Ship a browser extension/user script whose permission and target-origin allowlist are shown
   to the user before apply.

In both models require a dry-run, origin confirmation, nonce, TTL, visible insertion id,
`postMessage` source/origin validation, undo/revoke, and audit receipt. Refuse wildcard origins,
`javascript:` URLs, unbounded `innerHTML`, and hidden background injection.

### Exit tests for future work

- Player routes serve only the approved asset; no live tool endpoint is reachable.
- `HEAD` and byte ranges work; wrong range/MIME/path/host produces a typed refusal.
- Expiry and revoke make both page and media URL unusable.
- `media.embed.plan` changes nothing; `apply` requires explicit approval and an allowlisted
  origin; `revoke` removes the insertion.
- Upload retry is idempotent and a failed upload cannot claim `public`.
- Secret scanning finds no token in logs, manifests, URLs, captions, or test artifacts.

## 8. Defect, bug, and gap register

The detailed evidence and focused-test plan are in [`technical-audit.md`](technical-audit.md).
These are the gates that affect this marketing workflow:

| ID | Priority | Finding | Impact |
| --- | --- | --- | --- |
| D-01 | P0 | `Panel` and `VizServer` bind without auth; panel responses set `Access-Control-Allow-Origin: *`; no origin/CSRF policy exists. | A public bind can expose state and code-mutating HTTP routes. |
| D-02 | P0 | `live.panel_repl=false` blocks `POST /repl` but not `POST /api/control`; control still starts/stops/patches/renders/restores. | The documented “read-only viewer” mode is false. |
| D-03 | P1 | `Panel.url`/`VizServer.url` rewrite `0.0.0.0` to `127.0.0.1`. | A remote/proxied browser receives an unusable URL; recording instructions can fail. |
| D-04 | P1 | Panel activity reads `engine._ledger`, while `Engine` stores `engine.ledger`. | `/api/activity` silently reports empty activity even after calls. |
| D-05 | P1 | `Panel.start()` appends a frame listener; `Panel.stop()` never removes it. | Repeated serve/stop leaks old panels and multiplies callbacks. |
| D-06 | P1 | `[live].max_source_bytes` exists but is not enforced by `LiveManager`/`LiveProgram`. | Resource limit in configuration is ineffective. |
| D-07 | P1 | `/api/control` has no equivalent of `/repl`'s 64 KiB request cap. | A reachable panel has an avoidable request-body memory/DoS gap. |
| D-08 | P1 | `pub.inject` plans all files first but writes sequentially; a later write failure can leave earlier files changed. | “No partial publish” is not atomic under concurrent edits/I/O failure. |
| D-09 | P1 | `RunRecorder` is an internal JSONL tool trace, is not automatically wired to showcase runs, and stores full envelopes/baselines. | Reproducibility is manual and the artifact may contain sensitive data. |
| D-10 | P1 | No player, recorder, uploader, captions/transcript pipeline, asset manifest, or nester exists. | The requested media lifecycle cannot be completed by repository tools. |
| D-11 | P2 | Panel has no `do_HEAD`/`OPTIONS`; `VizMount` accepts `HEAD` but returns a body, while `VizServer.do_HEAD()` calls `do_GET()`. | Browser/proxy/embed health checks and CORS preflight are not protocol-complete. |
| D-12 | P2 | `Access-Control-Allow-Origin: *` also exposes standalone catalog/runtime metadata cross-origin. | Public runtime overlays can disclose more than a static catalog. |
| D-13 | P2 | Live dependency reload is scoped to same-tree imports; cross-package closure is explicitly “next” in `docs/LIVE-IMPL-PLAN.md`. | Multi-package showcase edits may not refresh all dependents. |

### Environment errors observed during this audit

- `python -m compileall -q skeletonkey features`: passed.
- `PYTHONPATH=. python -m skeletonkey.cli --cwd . viz --summary`: passed; current source
  reported 81 registered / 79 advertised tools.
- `pytest -q tests/test_docs.py`: could not run because `pytest` is not installed in this
  checkout. Install `.[dev]` before treating the test gate as passed.
- `doctor` in the bare checkout reported a partial state directory and an unavailable MCP
  stdio probe. Use a fresh state directory and install `.[mcp]` for the full doctor claim;
  do not use that failed run as a marketing screenshot.
- Manual probes reproduced D-02, D-03, D-04, D-05, and D-06. They are not hypothetical
  findings; add regression tests before public capture.

## 9. Remediation and execution milestones

### M0 — make rehearsal truthful

- Fix D-01/D-02 first: origin/auth/CSRF or strict loopback enforcement; gate all mutating
  routes when `panel_repl=false`; add regression tests for `/repl`, `/api/control`, CORS,
  `OPTIONS`, and hostile origins.
- Fix D-03/D-05/D-06/D-07 and D-04; add tests for remote URL construction, listener count,
  source-size refusal, request-body cap, and non-empty activity rows.
- Fix or explicitly scope D-08 before using `pub.inject` in a release workflow.
- Add a `marketing.showcase/1` schema or validation script; do not rely on prose sidecars.

**Exit:** focused live/viz/publish tests pass; private panel is the only accepted capture mode;
security reviewer signs the exposure matrix.

### M1 — deterministic showcase harness

- Add fixture generation under a disposable temp root; seed all files and fake markers.
- Add a harness that runs each tool sequence, captures normalized envelopes, computes fixture
  and toolset hashes, and writes the sidecar.
- Use `RunRecorder` only for tool proof; add secret/path redaction before copying it into the
  marketing artifact directory.
- Add a no-browser smoke test for every row: expected tool ids, result keys, cleanup, and
  replay/undo outcome.

**Exit:** every required row can run from a clean checkout with one documented command.

### M2 — local capture and review

- Rehearse and record `03-live-hmr` and `04-viewport` first; record `00`–`02` next; add
  integrations/publish/multimedia only if their backend receipts are clear.
- Capture narration separately; use [`scripting-is-fun.md`](scripting-is-fun.md) and never
  narrate a known unsupported feature as if shipped.
- Produce masters, captions, posters, sidecars, hashes, and rejected-take records.

**Exit:** reviewer approves content and security scan finds no secrets or false claims.

### M3 — upload and distribution

- Select the provider, upload the exact approved hash, verify from a clean browser, and record
  provider id/visibility/rollback information.
- Keep public visibility disabled until asset and caption playback is verified.

**Exit:** sidecar has provider asset id, reviewed URL, checksum, visibility, reviewer, and
cleanup/revoke procedure.

### M4 — optional media subsystem

Only start this milestone if a real product requirement remains after external recording. Build
`media.player.*`, `media.embed.*`, and `media.upload` as separately permissioned optional
surfaces under the requirements in Section 7. Do not widen `live.serve` to absorb them.
