# Marketing package: tool showcase recording and upload plan

**Workspace:** `plan`
**Workflow:** `workflows`
**Package:** `marketing package`
**Status:** discovery complete; recording workflow ready for execution
**Date:** 2026-09-19

## Objective

Turn the strongest, already-working parts of SkeletonKey / ToolForge into a small set of
short, honest product demonstrations. Every recording must connect a user problem to the
specific tools that solve it, show those tools operating in the repository, and leave behind
enough metadata to reproduce the shot and upload it later.

The package should make the following promise:

> The tool catalog chooses the right capability, the live viewport makes the work visible,
> and the safety and publishing surfaces explain what will happen before anything is shipped.

This is a marketing and recording plan, not a claim that the project already contains a
video production or hosting service.

## Definition of done

- [ ] Each selected showcase below has a named demo source, an exact tool sequence, a
      one-sentence narration, and a recorded screen capture.
- [ ] The recording shows a real result, not only a catalog card or a terminal command.
- [ ] Each clip has a reproducibility note: commit, command/tool arguments, host profile,
      demo input, and any optional dependency used.
- [ ] No credentials, personal paths, `.sk` state, or private workspace data appear in a
      frame, transcript, subtitle, thumbnail, or upload description.
- [ ] A human reviews the clip, transcript, captions, title, and target channel before upload.
- [ ] Uploads are made through an explicitly selected external host. The repository currently
      provides publishing knowledge and secret-safe placeholder injection, but not an upload
      client or a video player.

## Repository findings: player and nester check

The source, documentation, tests, and tool manifests were searched for video playback,
recording, browser automation, iframe embedding, domain injection, `player`, `nester`, and
related media routes. The result is **no dedicated video player or nester tool**.

| Existing surface | What it can do for a recording | What it cannot honestly be called |
| --- | --- | --- |
| `live.start` + `live.serve` | Start a Python live program and serve a browser preview with SVG frames, scene JSON, 3D view, HMR, and an in-page REPL. `sk dev --open` can open the preview locally. | A video player, screen recorder, or video host. It has no `<video>` playback, timeline, encoder, or upload route. |
| `live.render` + `live.scene` | Produce a current frame or manipulate the retained 2D/3D scene for a live demo. | A media playback API. The output is a live render, not a recorded video asset. |
| `sk dev` / `VizMount` | Run the live panel and expose the read-only tool catalog at `/tools` on the same development server. | A general website/domain injector. It only mounts SkeletonKey's catalog in its own server. |
| `VizServer` / `sk viz --serve` | Serve a read-only HTML tool catalog, with model, digest, explanation, and health routes. The page can link to a live panel. | A player or embed proxy. Its `panel_url`/`viz_url` relationship is a link only; it does not fetch, proxy, or inject into another domain. |
| `features/multimedia_gen/` | Generate images and stream STT/TTS test output; useful as supporting media for a future showcase. | A video playback, recording, editing, or upload subsystem. |
| `pub.platforms`, `pub.packaging`, `pub.testers`, `pub.placeholders`, `pub.inject` | Explain publishing targets, packaging, release checks, and safely inject stored values into workspace templates. | An uploader. `pub.inject` changes workspace files; it does not send a video to a service. |

### Safety implication

The live panel's REPL and control endpoints execute code. Keep it on loopback for local
recording, or use the sandbox's preview proxy only with an isolated demo workspace and a
short-lived process. In particular, do not present the merged `/tools` mount as a safe public
embed: `sk dev` puts the catalog and the executable live panel on the same server. If a
read-only catalog must be shared, prefer `sk export viz-html` or a standalone
`sk viz --serve` instance.

### Proposed gap, clearly marked as future work

If marketing needs an actual playback window or temporary website embed, design it as a new,
separately permissioned surface rather than stretching `live.serve`:

1. **Player service (proposed):** serve a local or explicitly hosted HTML5 player for a
   selected recording, with a returned URL, play/pause/seek, caption track, poster, and a
   revocable TTL. This is not implemented today.
2. **Nester service (proposed):** return a signed, expiring iframe/snippet for an allowlisted
   origin, or a browser-extension action that the user explicitly starts. It must not silently
   modify an arbitrary third-party domain. The existing `VizMount` route is an internal
   catalog mount, not this feature.
3. **Capture/upload adapters (proposed):** keep recording and upload adapters external or
   optional; record the provider, asset id, checksum, visibility, and revoke/delete action
   in a manifest. Never put provider secrets in a demo script or a URL.

Acceptance for that future work is: the player can be stopped and the URL becomes unusable;
the nester refuses an unallowlisted origin; CSP/SOP failures are reported rather than bypassed;
no request can execute the live REPL; and a dry-run shows exactly what route or snippet would be
created before a write or upload.

## Showcase-to-tool alignment

The first cut should be six clips. Keep each clip focused on one outcome and record the tool
call or CLI output immediately before the visible result so the relationship is clear.

| ID / target length | User story and showcase | Demo/source to use | Tools to show in order | Capture beats and evidence | Suggested asset |
| --- | --- | --- | --- | --- | --- |
| `01-discover` / 45–60s | Find the right capability without loading the whole toolset into context. | The registered catalog; `docs/TOOL-VISUALIZER.md`; `docs/TOOLSET.txt`. | `profile.probe` → `registry.search` → `registry.describe` → `planner.plan`. | Start with a plain-language need; show the ranked match, contract, risk, and replayable plan; finish with the selected call. | `01-adaptive-tool-discovery.mp4` |
| `02-safe-edit` / 60–90s | Make a real codebase change while preserving an undo path. | A disposable copy of a small fixture; `skills/fs-safe-refactor/`; `tests/test_fs_ops.py` and `tests/test_journal.py` as validation references. | `fs.search` → `fs.read` → `fs.patch` or `fs.write` → `fs.journal_list` → `fs.undo` / `fs.undo_task`. | Show the before state, targeted diff, undo token, and restored state. Do not use a destructive command merely for drama. | `02-safe-edit-and-undo.mp4` |
| `03-live-hmr` / 75–120s | Edit a running program without losing state or restarting the preview. | `examples/live_hmr/orbital.py`, which mirrors `skeletonkey/live/demos.py`; `docs/LIVE-HMR.md`. | `live.start` → `live.serve` → `live.status` → `live.repl` → save/reload → `live.render`; optionally `live.scene` or `live.snapshot`. | Show the orbital scene; change `hue` or camera state in the REPL; edit the watched file; show the counter/state surviving the reload and the frame changing. | `03-live-hmr-no-restart.mp4` |
| `04-viewport` / 60–90s | Understand the whole tool surface in the same viewport as the live demo. | `sk dev` with the orbital demo; `skeletonkey/viz/mount.py`; `docs/TOOL-VISUALIZER.md`; ADR 0017/0018. | `live.start` → `live.serve {mount_tools: true}` (or `sk dev`) → catalog routes; alternate safe view: `sk viz --serve` / `sk export viz-html`. | Show the frame at `/`, open `/tools`, inspect a tool card and its gate/explanation, then contrast the separate read-only catalog. | `04-dev-viewport-and-catalog.mp4` |
| `05-integrations` / 60–90s | Discover, validate, and load an integration without pretending an unavailable dependency is present. | `skeletonkey/integrations/examples/simple_demo.py`, `examples/rest_api.py`, and `docs/INTEGRATION-DEVELOPMENT.md`. | `integrations.discover` → `integrations.list` → `integrations.describe` → `integrations.validate` → `integrations.health` → `integrations.load`. | Show discovery, manifest/capabilities, a healthy zero-dependency example, and the honest optional-dependency result when applicable. | `05-integration-lifecycle.mp4` |
| `06-publish-safely` / 60–90s | Prepare release material without exposing credentials. | `docs/TOOL-CONTRACT.md` publishing section; `pub.*` tool manifests; `docs/SECURITY-MODEL.md`. | `pub.platforms` → `pub.packaging` → `pub.testers` → `pub.placeholders` → `pub.inject {dry_run: true}`; only after review, run the approved injection. | Use fake/demo markers; show the dry-run file list and missing-id refusal; show metadata-only store listing. Never record a real secret or a live account. | `06-secret-safe-publish-prep.mp4` |

The `features/multimedia_gen/` image, STT, and TTS modules can become a separate optional
clip after the core six. They should be labeled as generation/streaming support, not as a
video player. If a future player is implemented, add a seventh row only after its manifest,
tests, and threat model exist.

## Recording workflow

### 1. Prepare a clean, repeatable scene

1. Create a disposable workspace containing only the selected fixture and demo files.
2. Run `sk doctor`, `sk profile`, and `sk describe`; save their JSON/text output beside the
   shot manifest. If an optional backend is absent, keep the honest fallback or mark the shot
   blocked; do not edit the result to imply availability.
3. Use a stable commit and record the Python version, operating system, host profile, active
   tier, and toolset digest from `sk viz --summary`.
4. Use fake credentials and fake domains. Disable notifications, unrelated terminals, and
   personal browser profiles. Keep the live panel loopback-only unless the preview environment
   requires a public bind.
5. Preflight the exact tool sequence once without recording. Confirm the cleanup command and
   the undo/revoke path before beginning the take.

### 2. Record the take

For each row in the alignment table:

1. Open with the user problem in one sentence.
2. Show the relevant tool selection/contract briefly; avoid reading a long schema aloud.
3. Execute the smallest meaningful example.
4. Hold the result on screen long enough to see the change, status, or returned artifact.
5. State limits when they matter: for example, the catalog is read-only, the live panel
   executes code, or the repository has no player/uploader.
6. End with the reproducible command/tool call and the clip id, not an unverified marketing
   claim.

Recommended capture defaults are 16:9, 1080p, 30 fps, clean system audio, and a separate
lossless or high-bitrate master. Treat those as production defaults, not repository guarantees;
the recorder is external to this project.

### 3. Package the evidence

Create one sidecar record per clip. A JSON or Markdown record should include:

```text
clip_id: 03-live-hmr
source_commit: <git commit>
demo_source: examples/live_hmr/orbital.py
tool_sequence: live.start, live.serve, live.status, live.repl, live.render
command_or_arguments: <redacted reproducible call>
host_profile: <os / Python / optional backends>
toolset_digest: <from sk viz --summary or sk describe>
recorded_at: <UTC timestamp>
master: <local path or external asset id>
captions: <path or external asset id>
checksum: <SHA-256 of the uploaded master>
visibility: draft | unlisted | public
review: pending | approved | rejected
notes: <known limitations and fallback behavior>
```

Use `pub.platforms` and `pub.packaging` to choose the external destination and release form.
Use `pub.testers` for a release-check plan. Use `pub.placeholders` and `pub.inject` only for
fake/demo values or an approved release workspace; `pub.store_put` values must never be shown
on screen and the store is write-only.

### 4. Review, upload, and clean up

- Watch the complete clip with captions enabled; check tool names, URLs, paths, and claims.
- Search the export, transcript, subtitles, and thumbnail for tokens, passwords, private URLs,
  local usernames, and accidental `.sk` artifacts.
- Confirm the title and description identify the source commit and the actual feature shown.
- Upload through the chosen external provider; record the provider asset id and checksum in the
  sidecar. Keep visibility unlisted until the final review.
- Re-open the uploaded asset from a clean browser/profile, verify captions and poster, then
  promote visibility.
- Stop `live.serve`/`sk dev`, delete the disposable workspace, revoke any future player/embed
  URL, and remove test credentials. Retain only the approved master, sidecar, and captions.

## Narration and script handoff

The companion document [`scripting-is-fun.md`](scripting-is-fun.md) contains reusable narration
beats and a short script for the first recording. Scripts should describe observed behavior and
limitations, not promise an unimplemented player, nester, recorder, or uploader.

## Risks and gates

| Risk | Gate before recording/upload |
| --- | --- |
| A preview is mistaken for a video player | Label `live.serve`/`VizServer` as a live HTML/SVG preview; do not promise seekable playback. |
| A mounted catalog exposes the executable panel | Keep `sk dev` private; prefer standalone `sk viz --serve` or static `sk export viz-html` for sharing. |
| A recording leaks a secret or private domain | Fake values, clean browser profile, sidecar scan, human review, and no real `pub.store_put` during a take. |
| An optional dependency is absent | Show the fallback/skip honestly and record the host profile. |
| An upload cannot be reproduced or removed | Keep commit, toolset digest, checksum, provider id, visibility, and cleanup/revoke action. |
| A future nester modifies a third-party site unexpectedly | Require explicit origin allowlist, user action, TTL, stop/revoke, and a dry-run preview. |

## Next actions

1. Select one owner and one reviewer for clips `01`–`06`.
2. Create the disposable fixture workspace and sidecar template.
3. Rehearse `03-live-hmr` and `04-viewport`; these are the clearest visual differentiators.
4. Record the core six, review locally, and attach sidecars before choosing an upload host.
5. Decide whether the missing player/nester is a real product requirement. If yes, open a
   separate implementation plan with the proposed permission and expiry contract above; do
   not overload `live.serve` with arbitrary-domain injection.
