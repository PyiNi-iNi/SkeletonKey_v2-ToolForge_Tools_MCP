# ADR-0001 — Zero mandatory dependencies in the core

- **Status:** accepted
- **Date:** 2026-08
- **Deciders:** Dime (toolkit owner)

## Context

This toolkit is embedded in an agent runtime that ships on customer machines, inside CI
runners, and occasionally inside a container that has nothing but a Python interpreter.
Every mandatory dependency is (a) an install failure mode on a machine we do not control,
(b) a supply-chain surface we inherit, and (c) a version-resolution argument with whoever
is embedding us. The MCP SDK is a hard requirement only for the transport, not for the
engine, the shells, or the filesystem layer.

## Decision

`pyproject.toml` declares **`dependencies = []`**. Everything optional lives in extras:

| Extra | Brings in | Needed for |
| --- | --- | --- |
| `mcp` | `mcp>=2.1,<3`, `mcp-types` | `python -m skeletonkey.mcp` |
| `watch` | `watchfiles` | `tools.hot_reload` (P2) |
| `all` | `mcp`, `watch` | humans |
| `dev` | `pytest`, `pytest-asyncio`, `ruff` | contributing |

Consequences accepted: we write our own JSON-Schema subset (ADR-0004), our own TOML reads
via stdlib `tomllib`, our own NDJSON ledger instead of SQLite, and our own ANSI stripping
instead of `colorama`/`rich`.

`skeletonkey.core` must import with an empty `site-packages`. This is the promise, so it is
a test, not a comment (P6 makes it an enforced import-isolation check).

## Rejected alternatives

- **Depend on `pydantic` for validation/manifests.** Beautiful errors, one more
  major-version cliff, and a 6 MB install on machines whose whole point is being small.
  Our dataclasses + `from_dict` give the same ergonomics for the ~15 shapes we model.
- **Depend on `mcp` unconditionally.** Then `sk fs patch` fails to install on a machine
  that never wanted a server.
- **Vendor `jsonschema`.** Vendoring trades a dependency for a licensing/maintenance
  obligation; a 300-line subset with our error messages is cheaper and it never diverges
  from what our manifests actually use.

## Consequences

- (+) `pip install .` cannot fail for dependency reasons; offline/bootstrap installs work.
- (+) MCP churn is contained in `skeletonkey/mcp/adapter.py`; the wire tests speak raw
  JSON-RPC so they survive an SDK rename (they caught three real adapter bugs this way).
- (−) We own the sharp edges: TOML subset, schema subset, path handling for Windows we
  cannot test locally. Each one is why those files have the densest test coverage.
- (−) No `anyio`/`httpx` conveniences, so the streamable-http transport is a thin wrapper
  over the SDK's own server rather than our own asyncio plumbing.

## Verification

`tests/test_registry_config.py` (config/registry work with no extras present), plus CI
installing `.[dev,mcp]` — if `core` ever grows an import outside the stdlib, the ubuntu job
that installs only `.[dev]` fails first.
