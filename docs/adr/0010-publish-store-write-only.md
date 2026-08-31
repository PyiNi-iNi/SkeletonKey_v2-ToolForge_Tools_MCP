# ADR 0010: the publish store is write-only — location is the wall, injection is the only door

Date: 2026-08-31
Status: accepted
Affects: `core/publish.py`, `publish_data.py`, `core/config.py` (`PublishConfig`),
`core/manifest.py` (`secret_args`), `core/engine.py` (ledger redaction of declared
secret args), `core/redact.py` (bare `value` key), `tools/builtin.py` (`pub.*`),
`toolkit.py` (store wiring), `cli.py` (`sk pub`), `skills/publishing/`,
`tests/test_publish.py`, `tests/test_mcp_stdio.py`

## Context

Publishing an artifact (app, package, installer, store listing) requires platform
credentials — Play Console, App Store Connect, PyPI, GitHub, Stripe — and the
templates that carry them. Before this phase an agent either pasted secrets into
workspace files by hand (where the fs tools, the journal, the ledger previews and
the host's own context could all see them) or had no tooling at all for the "where
do I even publish this" half of the job (console URLs, payment setup, packaging
paths).

The hard requirement: **once a secret is handed to the toolkit, the agent must not
be able to read it back.** An agent that can `fs.read` its workspace and call
arbitrary tools is exactly the component a prompt-injected file could steer into
"helpfully" printing a credential.

## Options considered

1. **OS keyring** (keyring/SecretService/wincred) for at-rest encryption.
   Rejected: it is a third-party dependency (the zero-mandatory-deps rule, ADR-0001),
   it behaves differently on headless CI, and it would make the store a place where
   *values come back out* — the keyring API hands the secret to the caller, which is
   precisely the read path we are refusing to build.
2. **Environment variables** (`PUB_*`) as the store. Rejected: env is visible to
   every child process and to `shell.sessions`-style introspection, it cannot be
   listed per-id, and there is no write-only story at all.
3. **A store file *inside* the workspace**, protected by an fs deny rule. Rejected:
   deny rules are policy data an agent can see (`registry`/config surfaces) and that
   P3's grant machinery can interact with; a wall that lives in the same rule table
   as everything the agent may be granted is the wrong wall. It also lands the store
   inside the journal's reach.
4. **A store file *outside* the workspace roots, write-only by API.** Chosen: the
   fs sandbox (the wall every `fs.*` call already crosses) becomes the protection
   without any new rule, and the "write-only" property is a statement about the tool
   surface — no advertised tool returns a raw value.

For redacting the `value` argument in the audit trail, (a) relying on the
pattern-matchers was rejected (a store value is arbitrary text; a PyPI token does
not look like a GitHub token), and (b) masking every arg named `value` in every
tool was rejected as too blunt on its own — instead the manifest declares
`secret_args` and the engine redacts exactly those keys, with the key-name matcher
extended (bare `value` segment) as an independent backstop.

## Decision

- **The store is JSON at `<user config dir>/skeletonkey/publish/store.json` by
  default** (`[publish] store_path` override), written `0600` best-effort, *outside
  the workspace roots*. `fs.*` tools cannot reach it; that is the wall, and it is
  tested.
- **The tool surface is write-only.** `pub.store_put` stores; `pub.store_list`
  returns metadata plus a short non-inverting mask (`ab…YZ(19)`); `pub.store_delete`
  is destructive and *irreversible* (the store is outside the workspace journal —
  said so in the result, not discovered). No tool returns a raw value.
- **The only value flow out of the process is `pub.inject`**, which replaces
  `{{PUB.<id>}}` markers in workspace files through `fs.write` (`expect_sha`
  conflict detection, journaled, undoable via `fs.undo_task` on the call's own
  task id). It plans all files first and writes nothing if any marker is unbound:
  no partial publishes. `bindings` remaps a marker id to another store id.
- **Secrets in args are redacted by declaration.** `pub.store_put` declares
  `secret_args: ["value"]`; the engine replaces exactly those keys before the
  ledger row. Independently, `redact_obj` now masks bare `value`-named keys.
- **The knowledge bases are code, not model memory.** `publish_data.py` holds the
  platform/payment/packaging entries (real console/docs URLs, concise steps,
  credential kinds, placeholder examples); `pub.platforms`/`pub.payments`/
  `pub.packaging` surface them; `pub.testers` emits a machine-executable release
  test plan that references `{{PUB.<id>}}` and never raw values.

## Consequences

- A store id is a *capability*: whoever can run `pub.inject` can materialize every
  stored secret into any file the sandbox allows writing. The sandbox and the
  approval policy are therefore the second wall, and they already exist.
- `shell.run` can still read the store file if the user's OS permissions allow:
  the wall is against *fs tools*, not against the process's own user. Stated in
  SECURITY-MODEL alongside the journal/ledger, not papered over.
- Values are plaintext on disk, protected by location and file permissions, **not
  encryption** — a deliberate trade of the zero-dependency rule (ADR-0001), stated
  in SECURITY-MODEL.
- `pub.store_delete` cannot be undone by `fs.undo*`; the honest alternative (rotate
  = put again, then delete) is in the tool's anti-patterns.
- A masked listing reveals first/last two chars and length of values longer than 4
  chars — enough to identify a credential, not enough to use one. For short values
  it is just `****`.
