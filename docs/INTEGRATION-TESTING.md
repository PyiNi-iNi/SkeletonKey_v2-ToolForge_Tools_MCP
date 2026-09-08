# Integration testing guidelines

Tests are part of an integration's contract. Keep them deterministic, local,
and safe to run in parallel.

## Three layers

### 1. Registry and validation tests

Call `validate_integration(name)` and `validate_registry()` to prove metadata,
dependencies, tools, config, source-secret scanning, and `min_engine`
compatibility. Assert stable issue codes rather than prose. Include a test
that every advertised tool ID maps to a callable handler.

These tests must not make network calls, read a developer's real credentials,
or write `.sk` state in the repository. Use a temporary state directory and
restore environment variables with `monkeypatch`.

### 2. Wire-level provider tests

Use an in-process `ThreadingHTTPServer` (or an equivalent local stub) and
point `base_url` at it. Record method, path, query, headers, and body. Cover:

* authentication header and absence/presence of credentials;
* URL quoting and query filtering;
* JSON and provider-specific body encoding;
* successful object and paginated responses;
* non-2xx responses, timeout, malformed payload, and remote error mapping;
* provider metadata such as request IDs or rate limits;
* cleanup and reload.

Never call a real SaaS endpoint. Bind test servers to an ephemeral address,
shut them down in a `finally`, and avoid assumptions about `localhost` DNS.
The `RestIntegration` base makes the shared request/envelope behavior easy to
exercise with a tiny fixture subclass.

### 3. Engine and MCP contract tests

Load through the public `integrations.load` path, call the resulting tool
through the engine, and unload it again. Add a wire-level MCP test only when
the integration is intended for that surface. Verify error codes remain
`BAD_ARGS`, `REMOTE`, or `TIMEOUT` as appropriate; do not re-wrap a remote
error into a generic local failure.

## Concurrency and cleanup

The engine dispatches handlers on a pool. Run a small concurrent test against
the stub server when an integration has mutable state, and assert requests do
not leak arguments or credentials into each other. A client may be shared only
if its library documents thread safety. Cleanup must run on unload and must
not use `asyncio.run()` from an already-running event loop.

## Optional SDKs and platform coverage

Tests for the stdlib path run everywhere. SDK-specific tests are skipped with a
clear reason when the optional package is unavailable; they must not turn an
optional dependency into an import-time failure. Keep the cross-platform path
free of POSIX-only APIs and exercise Windows-compatible URL, path, and
subprocess behavior where relevant.

## Suggested commands

```bash
.venv/bin/ruff check .
.venv/bin/pytest tests/test_integration_loader.py tests/test_integration_validation.py -q
.venv/bin/pytest tests/ -q
```

Before merging, inspect the validation report and confirm no test leaves a
`.sk` directory, credentials, temporary server, or generated artifact in the
working tree.
