# ADR 0013: remote MCP tools are pass-through with inherited risk and un-wrapped errors

Date: 2026-09-01
Status: accepted
Affects: `core/config.py` (`mcp.remotes`), `mcp/client.py` (connector),
`core/registry.py` (`stats`, `register`), `core/engine.py` (error mapping),
`core/errors.py` (`REMOTE`), `toolkit.py` (enrollment), `tests/test_remotes.py`,
`tests/test_mcp_stdio.py`

## Context

P5's original AC4: other MCP servers' tools must appear as `remote.<server>.<tool>` with
pass-through envelopes, inherited risk, `reversible: false`, `stateful: "host"`; a
denied/failed remote call returns the *remote* envelope's error code, never a wrapper;
and `registry.stats` keeps remote and local rows separate. The outer toolkit already has
gating, policy, approval, budgets, receipts and a journal; remote tools must fit that
machine without lying about what they are.

## Options considered

1. **Full proxy: re-validate, re-gate, re-approve every remote call locally.** Rejected:
   the remote server has its own policy; duplicating it would fabricate an authorization
   the local config never made, and a deny/approval split between the two would be a new
   security *surface*, not safety.
2. **Pass-through with identity masking (tools appear as local ids).** Rejected: the
   agent could not tell whose `fs.write` is whose, receipts and stats would mix
   providers, and `capabilities.explain` would attribute a remote gate to the local
   registry.
3. **Named pass-through (chosen):** `remote.<server>.<tool>` identity, inherited risk
   from the remote tool's annotations, honest `reversible: false` / `stateful: "host"`,
   remote error codes forwarded verbatim, and `source` on stats rows.

## Decision

- **Identity.** Id `remote.<server>.<tool>` (server name sanitised `[a-z0-9_-]`); group
  `remote`; `source: "remote:<server>"`; `provider: "remote:<server>"`;
  `capability` = the tool's own id (no capability race with local tools — a remote
  `fs.search` is a different provider and both stay callable by name).
- **Risk inheritance.** `readOnlyHint: true` ⇒ `risk: "read"`; `destructiveHint: true` ⇒
  `risk: "write"`; **absent or `false` ⇒ `risk: "write"`** — an unannotated remote tool
  gets the approval gate, because we cannot verify what a foreign server does with a
  call. Risk is never *lowered* by a remote server's claim.
- **Honesty fields.** `reversible: false` (a remote mutation is outside our journal),
  `stateful: "host"` (the remote host owns state; never `"none"`), `idempotent: false`
  (we don't know the remote's cache semantics), `parallel_safe: false` (unknown),
  `tier: "full"` (remote tools appear at the default tier; tier opts are about local
  context pressure). `requirements: ["mcp"]` gates the tool off (`DEPENDENCY_MISSING`)
  if the `mcp` extra is absent — the tool is *advertised* only when its server connected.
- **Error passthrough.** A skeletonkey-shaped remote response (`ok: false` envelope)
  yields a local error with the **same code string** and the remote `message`/`details`
  — the outer envelope is renamed `remote.<server>.<tool>` for attribution only. A
  non-skeletonkey remote error maps to the new `REMOTE` code (execution class, retryable
  false) with the remote's text preserved — never re-wrapped as `INTERNAL` or guessed.
  Transport failures map to `DEPENDENCY_MISSING` with the server name and the reason.
- **Enrollment.** The connector connects at build time (one thread + event loop per
  remote server, so the sync engine can call it). A server that fails to connect or
  handshake is a `registry.load_errors` entry (with the reason and `near` fix), not a
  silent absence — the host sees *why* `remote.<server>.*` isn't there.
- **Stats.** `registry.stats` rows gain `source` (manifest source); `stats(source=...)`
  filters, and the aggregate view gains `by_source` so remote and local counts are
  separate and readable.
- **Config.** `[mcp.remotes.<name>]` with `command` + `args` (stdio) or `url`
  (streamable-http), `enabled`, `timeout_s`. Explicit, never implicit: an env var or
  auto-discovered server is a surprise.

## Consequences

- A remote tool is only as trustworthy as its connected server: its calls pass through
  the local gate/policy/approval/budget/ledger *around* the call (attribution, budget,
  audit) but not *inside* it. Documented; a remote server is infrastructure, not an
  extension point for a security boundary.
- `REMOTE` is a new error code in the taxonomy — additive, documented in
  TOOL-CONTRACT §3/§7f.
- Remote tools do not participate in provider races (unique capability) and never win
  `tier` budget drops silently: they are `full`-tier and count toward the tier budget like
  any other tool, so `budget_drops` can name one if the caps are tight.
