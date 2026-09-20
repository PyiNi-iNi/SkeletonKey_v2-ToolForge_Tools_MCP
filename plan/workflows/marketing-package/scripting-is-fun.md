# scripting is fun

**Display name:** `scripting is fun`
**Location:** `plan` workspace → `workflows` → `marketing package`
**Role:** technical narration, shot list, and claim-control companion to [`plan.md`](plan.md)

## Authoring contract

A script may claim only what the take visibly proves. Each sentence must map to:

```text
claim -> tool id or command -> visible result -> limitation (when relevant)
```

Use the three-beat pattern:

1. **Problem:** what the operator needs to change, discover, or verify.
2. **Action:** one exact tool call or command, with safe fixture data.
3. **Proof:** returned envelope, changed frame, digest, undo receipt, or health result.

Do not call an SVG preview “video,” a JSONL replay “screen recording,” `VizMount` a website
injector, or `pub.inject` an uploader. The current panel executes Python; that fact belongs in
the script whenever the panel is visible.

## Tool-to-script cards

### 00 — catalog and discovery

**Tools:** `profile.probe`, `registry.search`, `registry.describe`, `planner.plan`
**Picture:** query → ranked result → contract/risk → replayable plan

> “I’ll describe the job instead of guessing a tool name. The host searches what it can
> actually advertise, shows the contract and risk, and returns a plan with runnable arguments.
> A gated capability is explained instead of silently omitted.”

**Proof:** host receipt, selected id, schema, gate reason, plan id and ordered steps.

### 01 — safe edit and undo

**Tools:** `fs.search`, `fs.read`, `fs.patch`, `fs.journal_list`, `fs.undo_task`
**Picture:** disposable fixture → diff → journal → restored digest

> “The fixture is disposable. I can inspect the match, apply a targeted patch, see the journal
> receipt, and undo the task. The safety property is part of the result, not a disclaimer after
> the edit.”

**Proof:** before/after SHA, unified diff, `undo_token`, journal entry, exact restored bytes.

### 02 — live HMR without restart

**Tools:** `live.start`, `live.serve`, `live.status`, `live.repl`, `live.render`; `fs.patch` for
an actual file edit
**Source:** `examples/live_hmr/orbital.py`
**Picture:** source → live SVG/3D frame → REPL change → file save → preserved state

> “This is a running Python namespace, not a pre-rendered animation. I’ll change the colour in
> the live namespace, then patch the watched file. The frame changes, the counter continues,
> and the reload report tells us whether the new source was accepted. If the source is broken,
> the old frame remains and the panel reports the failure.”

**Proof:** `frame_version` increases, `reloads` increases, `frame_error` is null for the good
take, and the SVG contains the changed value. Keep the panel loopback-only.

### 03 — private viewport versus shareable catalog

**Tools/commands:** `sk dev`, `live.serve` with `mount_tools`, `sk viz --serve`,
`sk export viz-html`
**Picture:** private `/` panel → `/tools` catalog → standalone/static catalog

> “The panel makes the running scene visible. The catalog explains capabilities, gates, risk,
> and relationships. The mounted catalog shares the panel’s executable origin, so it is for
> private development. For a read-only artifact I use the standalone server or exported HTML.”

**Proof:** `/tools/api/model`, `/tools/api/digest`, and `/healthz`; then a static HTML file that
opens without a server. Never imply that this injects into another domain.

### 04 — honest integration lifecycle

**Tools:** `integrations.discover`, `integrations.list`, `integrations.describe`,
`integrations.validate`, `integrations.health`, `integrations.load`
**Source:** `skeletonkey/integrations/examples/simple_demo.py`

> “An integration is not healthy because it appears in a directory. I’ll discover its manifest,
> inspect its capabilities, validate it, check health, and load it. If an optional dependency
> is missing, the result stays a real missing-dependency result.”

**Proof:** manifest id/version, capabilities, validation receipt, health status, loaded tool ids.

### 05 — publish preparation without secrets

**Tools:** `pub.platforms`, `pub.packaging`, `pub.testers`, `pub.placeholders`,
`pub.inject` with `dry_run: true`
**Picture:** fake `{{PUB.demo.token}}` marker → locations → dry-run plan

> “This is release preparation, not an upload. I’ll inspect the target, locate placeholders,
> and preview the files that would change. An unbound or expired marker refuses the real write;
> no credential appears in the recording.”

**Proof:** marker line/column, dry-run file list, missing/expired ids, `ready_to_publish` state.
Never run a real `pub.store_put` during a take.

## Complete first script: live HMR

**Title:** *Edit a live scene without a restart*
**Duration:** 75–90 seconds
**Source:** `examples/live_hmr/orbital.py`
**Environment:** clean fixture workspace, loopback panel, fake data, source commit recorded

| Time | Screen action | Narration |
| --- | --- | --- |
| 0:00–0:08 | Title, source path, commit badge. | “Can a running demo change without losing its state or restarting?” |
| 0:08–0:20 | Run `live.start`; open the private panel from `live.serve`. | “I’m loading the repository’s live HMR example and serving its local preview.” |
| 0:20–0:34 | Show orbital scene, counter, HUD, and status. | “The frame is rendered by Python. The counter and scene state belong to the running program.” |
| 0:34–0:48 | Run `live.repl` with a harmless `hue` assignment; show new SVG color. | “I’ll change one live value. The panel re-renders without replacing the process.” |
| 0:48–1:05 | Apply a controlled `fs.patch` or editor save; show reload report/status. | “Now the watched source changes. The runtime compiles it before patching and preserves the state the live session owns.” |
| 1:05–1:17 | Show frame version, reload count, and final frame. | “The result is observable: the frame advanced, the reload succeeded, and the state is still present.” |
| 1:17–1:28 | End card with limitation and source commit. | “This is a live HTML/SVG preview. Recording and uploading happen separately; this repository does not ship a video player or uploader.” |

### Take procedure

1. Start the recorder only after the tool sequence passes once without capture.
2. Keep `/repl` and `/api/control` inaccessible outside the local operator boundary; do not
   use a public bind until D-01/D-02 are fixed.
3. Record the exact REPL expression, patch file, result envelope, `toolset_digest`, Python/OS,
   source SHA, and sidecar id.
4. Do one good take. Make a separate take for a broken-save overlay; do not mix failure and
   success claims in one narration.
5. Stop the panel, verify the port is closed, hash the master/captions/poster, then review from
   a clean browser.

## Claim guardrails

| Say | Do not say unless a separately implemented feature proves it |
| --- | --- |
| “live preview,” “rendered SVG frame,” “tool catalog,” “replayable tool trace,” “journaled undo” | “video player,” “seekable playback,” “screen recording,” “embed anywhere,” “inject into any domain,” “one-click upload” |
| “advertised on this host” | “available on every host” |
| “optional backend is unavailable; fallback shown” | “production backend” when only a mock ran |
| “private development mount” | “safe public catalog” for merged `sk dev` |

## Title and metadata templates

**Title:** `[Outcome] with [tool or surface] — SkeletonKey demo`
**Thumbnail:** two to five words: `NO RESTART`, `UNDOABLE EDIT`, or `TOOLS EXPLAINED`
**Description:**

> Reproducible demo from `<commit>`, using `<tool sequence>` and `<demo source>`. Host:
> `<Python/OS/profile>`. Toolset digest: `<digest>`. Limitations: `<fallback/private panel/
> no player or uploader>`. This clip demonstrates repository behavior; it does not claim that
> SkeletonKey hosts or uploads video.

## Script review checklist

- Does every named tool occur in the actual sequence?
- Is the result visible and reproducible, rather than only asserted?
- Are preview, catalog, replay, and video terms technically distinct?
- Are optional dependencies and known limitations spoken where they affect the shot?
- Does the take avoid secrets, private paths, public panel routes, and stale URLs?
- Does the sidecar contain commit, fixture hash, toolset digest, tool sequence, media hashes,
  review status, provider asset id, visibility, and cleanup/revoke action?

If all six are true, scripting is fun: the words get out of the way and the tool does the
selling.
