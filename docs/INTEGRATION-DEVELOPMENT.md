# Integration Development Guide

This guide explains how to create, package, and distribute integrations for SkeletonKey.

## Overview

An **integration** is a self-contained module that extends SkeletonKey with tools, resources, and capabilities for a specific external system (API, SDK, platform, etc.). Integrations follow a standard structure and can be:

- **Built-in** — shipped with SkeletonKey
- **Installed from packs** — local directory, git repo, or PyPI package
- **Discovered dynamically** — via entry points, filesystem, or config

## Quick Start

### 1. Create an Integration Module

```python
# my_integration.py
from skeletonkey.integrations import (
    Integration,
    IntegrationConfig,
    IntegrationType,
    IntegrationCategory,
    IntegrationPlatform,
    IntegrationDependency,
    IntegrationCapability,
    register_integration,
)

@register_integration(
    name="my-api",
    title="My API Integration",
    version="1.0.0",
    integration_type=IntegrationType.API,
    category=IntegrationCategory.CLOUD,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="Integration with My Awesome API",
    author="Your Name",
    license="MIT",
    keywords=["api", "my-service"],
    dependencies=[
        IntegrationDependency(
            name="requests",
            version=">=2.28.0",
            optional=False,
            description="HTTP client",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to api.my-service.com",
            required=True,
        ),
    ],
)
class MyAPIIntegration(Integration):
    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
        self._api_key = None
        self._base_url = "https://api.my-service.com/v1"
    
    def initialize(self) -> bool:
        # Resolve API key from config/secrets/env
        self._api_key = (
            self.config.secrets.get("api_key") or
            self.config.config.get("api_key") or
            os.environ.get("MY_API_KEY")
        )
        if not self._api_key:
            return False
        self._initialized = True
        return True
    
    def cleanup(self) -> None:
        self._api_key = None
        self._initialized = False
    
    def check_health(self) -> tuple[str, str]:
        if not self._initialized:
            return ("unhealthy", "Not initialized")
        # Quick health check
        return ("healthy", "API reachable")
    
    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "my-api.get_resource",
                "title": "Get Resource",
                "description": "Fetch a resource from My API",
                "capability": "my-api.read",
                "risk": "read",
                "tier": "task",
                "tags": ["my-api", "read"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "resource_id": {"type": "string", "minLength": 1},
                    },
                    "required": ["resource_id"],
                },
            },
        ]
    
    def tool_get_resource(self, resource_id: str) -> dict[str, Any]:
        # Your implementation here
        import requests
        resp = requests.get(
            f"{self._base_url}/resources/{resource_id}",
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
```

### 2. Test Your Integration

```bash
# Create a test script
python -c "
from skeletonkey.integrations import IntegrationConfig
from my_integration import MyAPIIntegration

config = IntegrationConfig(
    name='my-api',
    integration_id='my-api',
    config={'api_key': 'test-key'},
)
integration = MyAPIIntegration(config)
print('Initialized:', integration.initialize())
print('Tools:', integration.get_tools())
print('Health:', integration.check_health())
"
```

### 3. Package as an Integration Pack

Create this directory structure:

```
my-integration-pack/
├── pack.json          # Pack manifest
├── my_integration.py  # Your integration module
└── requirements.txt   # Optional: Python dependencies
```

**pack.json:**
```json
{
  "schema_version": "1.0",
  "name": "my-integration-pack",
  "version": "1.0.0",
  "title": "My Integration Pack",
  "description": "Pack containing My API integration",
  "author": "Your Name",
  "license": "MIT",
  "integrations": [
    {
      "name": "my-api",
      "module": "my_integration",
      "class": "MyAPIIntegration"
    }
  ],
  "dependencies": [],
  "conflicts": []
}
```

### 4. Install and Use

```bash
# Install from local directory
sk integrations packs install ./my-integration-pack

# Or install from git
sk integrations packs install https://github.com/user/my-integration-pack.git

# Or from PyPI
sk integrations packs install my-integration-pack --source pypi

# List installed packs
sk integrations packs list

# Load the integration
sk integrations load my-api

# Use the tool
sk call my-api.get_resource '{"resource_id": "123"}'
```

## Integration Anatomy

### Required Components

| Component | Description |
|-----------|-------------|
| `@register_integration` | Decorator with metadata |
| `Integration` subclass | Main class with lifecycle methods |
| `initialize()` | Setup (returns `bool`) |
| `cleanup()` | Teardown |
| `check_health()` | Returns `(status, message)` |
| `get_tools()` | Returns list of tool definitions |
| `tool_*` methods | Handlers for each tool |

### Tool Definition Schema

Each tool in `get_tools()` must include:

```json
{
  "id": "integration-name.tool-name",
  "title": "Human readable title",
  "description": "What this tool does",
  "capability": "capability.name",
  "risk": "none|read|write|destructive|network|privileged",
  "tier": "core|task|full",
  "tags": ["tag1", "tag2"],
  "input_schema": {
    "type": "object",
    "properties": { ... },
    "required": [...]
  }
}
```

### Risk Levels

| Risk | Description | Requires Approval |
|------|-------------|-------------------|
| `none` | Read-only, no side effects | No |
| `read` | Reads data/files | No |
| `write` | Creates/modifies data | Yes (configurable) |
| `destructive` | Deletes/destroys data | Yes |
| `network` | Makes network calls | Yes (configurable) |
| `privileged` | Requires elevated permissions | Yes |

### Capability Declaration

Declare required capabilities in the registration:

```python
capabilities=[
    IntegrationCapability(
        name="network",
        description="Requires outbound HTTPS to api.example.com",
        required=True,
        default_granted=False,
    ),
    IntegrationCapability(
        name="filesystem",
        description="Needs read access to /tmp for caching",
        required=True,
        default_granted=False,
    ),
]
```

These capabilities are enforced by the sandbox when the integration is loaded.

## Configuration & Secrets

### Configuration

Integrations receive config via:

1. **Config file** (`[integrations.configs.my-api]`)
2. **Secrets store** (`integrations.secrets.set`)
3. **Environment variables** (`MY_API_KEY`)
4. **Load-time override** (`sk integrations load my-api --config '{"key": "val"}'`)

Access in your integration:

```python
def initialize(self) -> bool:
    # Priority: secrets > config > env
    api_key = (
        self.config.secrets.get("api_key") or
        self.config.config.get("api_key") or
        os.environ.get("MY_API_KEY")
    )
```

### Secrets Management

```bash
# Set a secret
sk integrations secrets set --action set --name my-api --key api_key --value "sk_live_..."

# List secrets
sk integrations secrets list --name my-api

# Get a secret (value masked)
sk integrations secrets get --name my-api --key api_key
```

## Sandbox Isolation

Integrations can declare sandbox constraints:

```python
@register_integration(
    ...
    sandbox={
        "enabled": True,
        "filesystem_isolation": True,
        "network_isolation": True,
        "allowed_paths": ["/tmp/my-api-*"],
        "allowed_hosts": ["api.my-service.com", "*.my-service.com"],
        "resource_limits": {
            "memory_mb": 256,
            "cpu_cores": 1,
            "timeout_seconds": 30,
        },
    },
)
```

| Setting | Effect |
|---------|--------|
| `filesystem_isolation` | Only `allowed_paths` accessible |
| `network_isolation` | Only `allowed_hosts` reachable |
| `allowed_paths` | Glob patterns for permitted paths |
| `denied_paths` | Glob patterns for forbidden paths |
| `allowed_hosts` | Host glob patterns (e.g. `api.*.com`) |
| `resource_limits.memory_mb` | Max memory (POSIX: `RLIMIT_AS`) |
| `resource_limits.cpu_cores` | CPU time limit (POSIX: `RLIMIT_CPU`) |
| `resource_limits.timeout_seconds` | Max operation duration |

## Packaging & Distribution

### Pack Manifest (pack.json)

```json
{
  "schema_version": "1.0",
  "name": "unique-pack-name",
  "version": "1.0.0",
  "title": "Display Title",
  "description": "What this pack provides",
  "author": "Author Name",
  "license": "MIT",
  "integrations": [
    {
      "name": "integration-name",
      "module": "module_name",
      "class": "ClassName"
    }
  ],
  "dependencies": [
    {
      "name": "other-pack",
      "version": ">=1.0.0",
      "source": "git",
      "optional": false
    }
  ],
  "conflicts": ["conflicting-pack-name"]
}
```

### Publishing to PyPI

```bash
# Build package
python -m build

# Publish
twine upload dist/*
```

Users can then install:
```bash
sk integrations packs install my-pack --source pypi
```

### Versioning

Use semantic versioning for packs:
- **Patch** (1.0.1): Bug fixes, no API changes
- **Minor** (1.1.0): New tools, backward compatible
- **Major** (2.0.0): Breaking changes

## Testing

### Unit Tests

```python
# test_my_integration.py
import pytest
from my_integration import MyAPIIntegration
from skeletonkey.integrations import IntegrationConfig

@pytest.fixture
def integration():
    config = IntegrationConfig(
        name="my-api",
        integration_id="my-api",
        config={"api_key": "test"},
    )
    return MyAPIIntegration(config)

def test_initialize(integration):
    assert integration.initialize() is True

def test_get_tools(integration):
    integration.initialize()
    tools = integration.get_tools()
    assert len(tools) > 0
    assert tools[0]["id"] == "my-api.get_resource"

def test_tool_handler(integration, monkeypatch):
    integration.initialize()
    # Mock the HTTP call
    def mock_get(*args, **kwargs):
        class MockResp:
            def raise_for_status(self): pass
            def json(self): return {"id": "123", "name": "Test"}
        return MockResp()
    monkeypatch.setattr("requests.get", mock_get)
    
    result = integration.tool_get_resource("123")
    assert result["id"] == "123"
```

### Validation

Run the built-in validator:

```bash
# Validate all integrations
sk integrations validate

# Validate specific integration
sk integrations validate --name my-api

# Include health checks
sk integrations validate --name my-api --include-health
```

The validator checks:
- Metadata sanity (name, version, title)
- Tool mapping (every tool has a handler)
- Tool schema validity (risk, tier, unique IDs)
- Dependencies (required packages importable)
- Security (mutating tools declare capabilities)
- Secret scanning (no hardcoded credentials)
- Engine compatibility (`min_engine`)

## Best Practices

### 1. Error Handling

```python
def tool_my_tool(self, arg: str) -> dict[str, Any]:
    try:
        result = self._api_call(arg)
        return result
    except requests.HTTPError as e:
        raise SkeletonKeyError(
            E.REMOTE,
            f"API error: {e.response.status_code}",
            details={"status": e.response.status_code}
        ) from e
    except requests.Timeout:
        raise SkeletonKeyError(E.TIMEOUT, "API request timed out")
    except Exception as e:
        raise SkeletonKeyError(E.INTERNAL, f"Unexpected error: {e}") from e
```

### 2. Idempotency

For write operations, support idempotency keys:

```python
def tool_create_resource(self, name: str, idempotency_key: str = None) -> dict:
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return self._api("POST", "/resources", body={"name": name}, headers=headers)
```

### 3. Pagination

```python
def tool_list_resources(self, limit: int = 10, cursor: str = None) -> dict:
    params = {"limit": min(limit, 100)}
    if cursor:
        params["cursor"] = cursor
    resp = self._api("GET", "/resources", query=params)
    return self._paged(resp, items_key="data")
```

### 4. Resource Cleanup

Always clean up in `cleanup()`:

```python
def cleanup(self) -> None:
    if hasattr(self, "_session"):
        self._session.close()
    if hasattr(self, "_temp_dir"):
        shutil.rmtree(self._temp_dir, ignore_errors=True)
    self._initialized = False
```

## Debugging

### Enable Debug Logging

```bash
export SKELETONKEY_LOG_LEVEL=DEBUG
sk integrations load my-api
```

### Inspect Integration State

```bash
# Describe integration
sk integrations describe my-api

# Check health
sk integrations health my-api

# View statistics
sk integrations stats
```

### Manual Testing

```python
from skeletonkey.toolkit import Toolkit
from my_integration import MyAPIIntegration

tk = Toolkit()
# Manually register and test
tk.engine.registry.register(MyAPIIntegration.get_manifest())
result = tk.engine.call("my-api.get_resource", {"resource_id": "123"})
print(result)
```

## Common Patterns

### REST API Integration

Inherit from `RestIntegration` for built-in HTTP handling:

```python
from skeletonkey.integrations import RestIntegration

class MyRestIntegration(RestIntegration):
    default_base_url = "https://api.example.com/v1"
    
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}
    
    def _encode_body(self, method: str, body: Any):
        if body is None or method.upper() in {"GET", "HEAD"}:
            return None, {}
        return json.dumps(body).encode(), {"Content-Type": "application/json"}
```

### SDK Wrapper

Wrap an existing Python SDK:

```python
class MySDKIntegration(Integration):
    def initialize(self) -> bool:
        import my_sdk
        self._client = my_sdk.Client(api_key=self._api_key)
        return True
    
    def tool_list_items(self) -> dict:
        return {"items": [item.to_dict() for item in self._client.items.list()]}
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Integration not found" | Check `pack.json` name matches integration name |
| "Dependency not importable" | Add to `requirements.txt` or `dependencies` in registration |
| "Sandbox violation" | Check `allowed_paths`/`allowed_hosts` in sandbox config |
| "Tool not callable" | Verify `tool_<suffix>` method exists and is public |
| "Health check fails" | Implement `check_health()` to return `("healthy", "msg")` |

## Reference

- **Schema**: `schemas/integration-manifest.schema.json`
- **Examples**: `skeletonkey/integrations/examples/`
- **Built-in integrations**: `skeletonkey/integrations/categories/`
- **CLI**: `sk integrations --help`
- **Tools**: `sk call integrations.list --help`