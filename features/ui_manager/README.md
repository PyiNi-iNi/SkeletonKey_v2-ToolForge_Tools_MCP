# features/ui_manager — Gradio UI, bake-off mode, state, authentication

User-facing layer. Gradio is optional; the same auth/state/bake-off cores
power a headless console UI and a compact HTTP auth server, so the
feature (and its tests) run with zero heavy deps.

## Architecture

```
auth.py        NativeAuth (scrypt + sessions + lockout), DeviceFlow (RFC 8628)
state.py       StateStore: NDJSON journal + snapshot per user (replayable)
bakeoff.py     BakeOff: N models, one prompt, live events + tok/s table
app.py         build_app() -> Gradio Blocks (lazy) | HeadlessUI (terminal)
server.py      HTTP auth: login/logout/me + device grant endpoints
```

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_UI_STATE_DIR` | `~/.skeletonkey/ui-state` | state store root |
| `SK_UI_USERS` | *(unset → in-memory)* | NDJSON user journal path |
| `SK_UI_SESSION_TTL` | `3600` | session lifetime (seconds) |
| `SK_UI_MAX_ATTEMPTS` | `5` | failed logins before lockout |
| `SK_UI_LOCKOUT` | `300` | lockout duration (seconds) |

## Authentication

- **Native**: scrypt password hashes (per-user salt), opaque 32-byte
  server-side sessions, TTL expiry, attempt counting + lockout.
- **OAuth device grant** (RFC 8628-shaped, self-hosted): headless hosts
  print a `user_code`, the user approves on any device, polling yields a
  session. Wire-compatible with any OAuth client that speaks the device
  flow against our endpoints.

```
POST /auth/register  POST /auth/login   POST /auth/logout   GET /auth/me
POST /device/code    POST /device/approve   POST /device/token (424 while pending)
```

## Bake-off mode

One prompt through every registered model, async and bounded.
`BakeOff.run()` returns per-model `BakeOffEntry` (text, tokens, tok/s,
meets-80 target, ok/error); `BakeOff.astream()` interleaves live
`bakeoff_start / bakeoff_delta / bakeoff_done` events for side-by-side
rendering; `BakeOff.render()` is the plain-text table.

## State preservation

Per user: `state.json` (latest snapshot, atomic replace) +
`journal.ndjson` (append-only, replayable audit). `StateStore.replay`
gives every save event in order — the same ledger convention the
toolkit uses elsewhere.

## Usage

```python
from features.ui_manager import HeadlessUI, StateStore
from features.inference_engine import InferenceEngine

engine = InferenceEngine()
ui = HeadlessUI(engine, models=["mock-1b"], state=StateStore("/tmp/ui"))
lines = await ui.repl(["/models", "/bakeoff hello", "/quit"])

# browser UI (needs gradio):
app = build_app(engine, auth=auth, state=store, models=engine.model_ids())
```

## Dependencies

Core: none. UI: `gradio>=4.20`. See `requirements.txt`. Cross-platform
(Linux/macOS/Windows/Docker); stdlib only on the hot paths.

## Tests

`tests/` — scrypt round trip + salt uniqueness, login/lockout/expiry
(injectable clock), session revocation, device flow full lifecycle +
expiry, state save/load/replay + per-user isolation, bake-off run +
live event stream + render, headless REPL commands end to end, HTTP
auth server (register/login/me/device), and Gradio-lazy fallback error.
