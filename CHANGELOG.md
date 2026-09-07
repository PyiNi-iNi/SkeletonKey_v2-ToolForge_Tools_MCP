# Changelog

## 0.2.0 — 2026-09-07

First tagged release. Everything below is new since the `0.1.0` working snapshot.

- **Publish expiry & rotation**: per-entry deadlines on `pub.store_put`
  (`expires_in_s`), an `expired` marker status, inject refusing expired entries
  with a rotate hint, and re-put clearing the deadline
  (`docs/TOOL-CONTRACT.md` §7d).
- **Whole-surface gate explanation**: new read-only core-tier
  `registry.explain_all` — one call explains every gate, so agents stop guessing
  why a tool is withheld (62 registered / 60 advertised in-repo).
- **Evals**: 27-task router suite with two publish tasks (inject; expired-then-
  rotate-and-recover), both routing top-5; `eval_suite` and `replay` pin the
  publish store to scratch state, never the operator's real credential store.
- **Security pass**: `tests/test_redteam.py` — seeded property tests over the
  sentinel parser and path normalization, plus one executable test per row of
  the bypass matrix (`docs/SECURITY-MODEL.md`); `pip-audit` clean (the core
  ships zero third-party runtime dependencies).
- **Config**: `config/skeletonkey.example.toml` documents `[publish]`.
- **CI**: ubuntu + windows matrix (`.github/workflows/ci.yml`).

## 0.1.0 — working snapshot

Untagged development snapshot: ToolForge v2 core — fs/shell/registry/policy,
journal + ledger, skills, publish flow, live HMR/REPL, and the MCP transports.
