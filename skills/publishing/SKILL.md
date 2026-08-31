---
name: publishing
description: >-
  Ship an artifact with credentials you never see again: store secrets write-only,
  scan templates for {{PUB.<id>}} markers, inject through the journal, and pull
  platform/payment/packaging steps from the built-in knowledge base.
when_to_use: >-
  Publishing or releasing anything - app stores, PyPI/npm, GitHub Releases,
  installers, a custom domain - or wiring credentials into deploy templates, or
  planning who tests the release.
version: "1"
tags: [publish, release, deploy, credentials, secrets, store, packaging]
priority: 60
requires: [fs]
allowed-tools: [pub.store_put, pub.store_list, pub.store_delete, pub.placeholders, pub.inject, pub.platforms, pub.payments, pub.packaging, pub.testers, fs.read, fs.write, fs.undo, fs.undo_task, fs.journal_list, registry.list, registry.search]
---

# Publishing

The store is **write-only to you**: after `pub.store_put`, no tool gives the value
back. The only way a value leaves the process is `pub.inject` writing it into a
file you can read. Build every publish on that one-way door.

## 1. Store, then forget

```
pub.store_put {id: "pypi.token", kind: "token", value: "...", note: "CI, created 2026-08-31"}
pub.store_list {}                # metadata + a short mask only
```

- `id` is `[a-z0-9][a-z0-9._-]{0,63}` — use `platform.thing` (e.g. `pypi.token`,
  `google_play.token`, `stripe.secret_key`).
- `kind` is validated (`token`, `api_key`, `client_secret`, `oauth_token`,
  `password`, `email`, `phone`, `two_factor`, `social_account`, `signing_key`,
  `certificate`, `webhook`, `other`).
- The value is redacted from the ledger automatically. Never type it into a file,
  a command, or a note. To change a credential: `pub.store_put` the same id again
  (rotation); `pub.store_delete` is irreversible (the store is outside the
  journal).

## 2. Markers carry the shape, the store carries the value

Templates hold `{{PUB.<id>}}` — exactly the store id:

```
PYPY_TOKEN={{PUB.pypi.token}}
TWINE_ARGS=--username __token__ --password {{PUB.pypi.token}}
```

Scan before you publish; publish only when nothing is missing:

```
pub.placeholders {path: "."}     # every marker: file, line, column, bound/missing
```

`ready_to_publish: false` means store the missing ids (or map them with
`bindings`) and re-scan. Do not hand-paste values to "fix" a missing marker.

## 3. Inject is the only door - and it is undoable

```
pub.inject {path: "deploy", dry_run: true}   # the plan, zero writes
pub.inject {path: "deploy"}                  # writes, journaled, expect_sha-protected
```

- If any marker is unbound, **no file is written** (no partial publishes).
- Each written file is journaled; `data.undo` points at `fs.undo_task` scoped to
  *this* call - undoing the publish reverts nothing else you did.
- `bindings: {"marker.id": "other.store.id"}` repoints a marker at a different
  stored credential without touching the template.
- Denied files (e.g. `.env`) are skipped with a note, not a failure.

## 4. The knowledge base is a tool, not your memory

```
pub.platforms   {}            # list: google_play, apple_appstore, github, pypi, npm, custom
pub.platforms   {name: "pypi"}
pub.payments    {provider: "stripe"}   # stripe, paddle, google_play_billing, apple_iap
pub.packaging   {target: "winget"}     # pypi, github_release, windows_installer, msi, scoop,
                                       # chocolatey, winget, homebrew, self_hosted
```

Each entry: console/docs URLs, numbered steps, the credentials to store (with
suggested ids), and a placeholder example. Apple Pay checkout is a PSP feature
(Stripe/Paddle), not a direct Apple API - the entry says so.

## 5. Testers: a plan the loop can execute

```
pub.testers {platform: "pypi", packaging: "pypi", version: "1.2.3"}
```

Returns ordered steps (preflight → build → publish → verify), each a tool call or
command with an acceptance line and an on-fail behavior, plus a stop rule. Plans
reference `{{PUB.<id>}}`, never values - run `pub.inject` before any step that
needs a secret.
