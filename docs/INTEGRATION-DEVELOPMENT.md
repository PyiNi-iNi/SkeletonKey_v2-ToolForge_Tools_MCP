# Integration development guide

This guide is the implementation contract for SkeletonKey integrations. Read
[TOOL-CONTRACT.md](TOOL-CONTRACT.md), especially section 8, before adding a
new tool. Integrations are optional adapters: the core remains Python 3.11+
and zero mandatory dependencies.

## Shape of an integration

Create a subclass of `skeletonkey.integrations.Integration` (or
`RestIntegration` for a JSON HTTP API), implement `initialize`, `cleanup`, and
`get_tools`, then add one `tool_<suffix>` method for every advertised tool.
The bridge maps `provider.resource.action` to `tool_resource_action`.
Handlers are synchronous and are run by the engine's worker pool, so do not
store request-specific data on the instance and do not assume calls are
serialized. Protect shared caches with a lock or avoid them.

The stdlib `urllib` transport is preferred. SDKs may be optional accelerators,
but an import at module scope must not make the integration impossible to
load. Never log or put credentials in tool results, errors, metadata, or
source-controlled examples.

## Registration

The decorator is the clearest registration form:

```python
from skeletonkey.integrations import Integration, IntegrationConfig, register_integration

@register_integration(
    name="acme",
    title="Acme API",
    version="1.0.0",
    integration_type=IntegrationType.API,
    category=IntegrationCategory.WEB,
    description="Acme's API",
    conflicts=["acme-legacy"],
    metadata={"min_engine": ">=0.2.0"},
)
class AcmeIntegration(Integration):
    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
    # lifecycle and tools...
```

The full surface is also available through
`registry.register(cls, info=...)` and `registry.register_integration(info)`
for loader/pack code. Keep the metadata consistent across paths. Supported
metadata includes the normal `IntegrationInfo` fields (name, title, version,
type, category, platform, description, author, license, keywords,
dependencies, capabilities), plus:

* `conflicts`: integration names that cannot be loaded together;
* `metadata`: extension data, including the `min_engine` compatibility gate.

A class convention (`_integration_conflicts` and `_integration_metadata`) is
used when a class is discovered or registered later. Do not mutate the shared
class lists; assign a new list/dict during registration.

## Discovery, registration, and loading

Discovery and registration are deliberately separate:

1. **Discovery** finds metadata (manifest, entry point, or filesystem module).
2. **Registration** makes the Python integration class available to the
   registry.
3. **Loading** constructs that registered class, validates its config,
   initializes it, and registers its tools.

A discovered manifest is not a class. If metadata is discovered but its class
has not been registered, loading fails with the explicit `class not found`
load error; it must not crash or silently fabricate an integration. A dynamic
module can call `register_integration` while it is imported, or use the
registry's class registration API before loading. Use loader results and their
`errors` field in callers.

Lifecycle rules: initialization must be repeatable enough for reload, cleanup
must release clients and secrets, and a failed initialization must not leave
advertised tools behind. Use `integrations.load`/`integrations.unload` in
wire-level checks rather than reaching into private registry state.

## Configuration and secrets

Read configuration from `IntegrationConfig.config`; read secret values from
its secret store or environment fallback. Document accepted keys and
precedence in the module docstring. Validate required values with
`SkeletonKeyError(E.BAD_ARGS, ...)`, naming a safe source and remediation
hint. Do not include the secret itself in `details`. Use `RestIntegration`'s
hooks for provider-specific auth and body encoding.

## Validation as a release gate

Run `validate_registry()` before shipping. The stable check codes are:

* `metadata` — required metadata and version shape;
* `dependencies` — required optional-SDK availability;
* `tools` — tool IDs, schemas, and callable `tool_*` handlers;
* `config` — configuration validation;
* `secrets` — secret-looking literals in the integration's own source;
* `compatibility` — `metadata["min_engine"]` versus the engine version.

`secrets` findings are warnings, but a release should have zero findings.
Unparseable `min_engine` is a warning; an unsatisfied requirement is an
error. A CI gate should require every report to be valid and should fail on
secret findings. The checks are import-level and side-effect free; network
behavior belongs in a stub-server test.

## Checklist

- [ ] subclass and registration metadata are complete;
- [ ] every advertised tool has a handler and a wire-safe schema;
- [ ] no mandatory SDK import or credential in source;
- [ ] lifecycle, error codes, timeout, and cleanup are tested;
- [ ] `pytest`, `ruff check .`, and `validate_registry()` are clean;
- [ ] docs include configuration, permissions, and provider behavior.
