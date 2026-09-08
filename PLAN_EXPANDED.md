# SkeletonKey / ToolForge v2 — EXPANDED Phase Plan
## Comprehensive Integration & Ecosystem Roadmap

**Document Status:** Active | **Last Updated:** 2026-09-07 | **Version:** 2.0-EXPANDED

---

## Executive Summary

This document **expands** the original [`PLAN.md`](PLAN.md) to include comprehensive support for:

- **SDKs & Client Libraries** - First-class integration patterns for external services
- **External APIs** - REST, GraphQL, gRPC, WebSocket API connectors
- **API Generators** - OpenAPI/Swagger, Protobuf, JSON Schema → Tool manifests
- **Frameworks** - Django, FastAPI, Flask, Litestar plugin systems
- **Toolchains** - LangChain, LlamaIndex, CrewAI, AutoGen bridges
- **CLIs** - Rich, Click, Typer, argparse tool generators
- **Runtimes** - Python, Node.js, Deno, Go, Rust protocol bindings
- **Protocol Bindings** - MCP, JSON-RPC, gRPC, WebSocket, SSE, REST
- **Codegen Systems** - Manifest → SDK, Schema → Tools, API → Client
- **Platform DevKits** - AWS, GCP, Azure, Vercel, Supabase, Firebase
- **Domain Integrations**
  - 3D Modeling (Blender, Three.js, CAD)
  - Billing & Payment (Stripe, Paddle, Lemon Squeezy)
  - Databases (Postgres, MongoDB, SQLite, Redis)
  - Agent Harnesses (LangChain, Autogen, CrewAI, Microsoft Semantic Kernel)

**Vision:** SkeletonKey becomes the **universal integration layer** for AI agents and autonomous systems, providing a **single, consistent interface** to any external system while maintaining our core principles: reversibility, safety, and observability.

---

## Phase Status Overview

| Phase | Name | Status | Start | End | Lead | Complexity |
|-------|------|--------|-------|-----|------|------------|
| P0 | Core Toolkit | ✅ Shipped | 2026-01 | 2026-03 | - | High |
| P1 | Filesystem & Shell | ✅ Shipped | 2026-03 | 2026-04 | - | High |
| P2 | MCP Server & Skills | ✅ Shipped | 2026-04 | 2026-05 | - | High |
| P3 | Policy & Safety | ✅ Shipped | 2026-05 | 2026-06 | - | High |
| P4 | Discovery & Routing | ✅ Shipped | 2026-06 | 2026-07 | - | High |
| **P5** | **Integration Foundation** | 🟡 **Active** | 2026-09 | 2026-11 | - | **Very High** |
| P6 | SDK & API Framework | 🔲 Planned | 2026-11 | 2026-12 | - | High |
| P7 | Protocol Bindings | 🔲 Planned | 2026-12 | 2027-01 | - | High |
| P8 | Codegen Systems | 🔲 Planned | 2027-01 | 2027-02 | - | Very High |
| P9 | Platform DevKits | 🔲 Planned | 2027-02 | 2027-04 | - | Very High |
| P10 | Domain Integrations | 🔲 Planned | 2027-04 | 2027-06 | - | Very High |
| P11 | Ecosystem & Distribution | 🔲 Planned | 2027-06 | 2027-08 | - | High |

---

## Phase P5: Integration Foundation (ACTIVE - 2026-09 to 2026-11)

**Goal:** Establish the architectural foundation for all external integrations.

### P5.0: Project Indexer & Discovery (Current Work)
- [x] Project scanning and indexing system
- [x] Type detection (skill, tool-pack, package, MCP server)
- [x] CLI integration (`sk projects list`)
- [ ] Auto-discovery on startup
- [ ] Watch mode for project changes
- [ ] Project metadata caching

### P5.1: Integration Registry
**New Component:** `skeletonkey/integrations/`

```
skeletonkey/integrations/
├── __init__.py          # Integration base classes
├── registry.py          # Integration catalog
├── discovery.py         # Auto-discovery of integrations
├── health.py            # Integration health checks
├── config.py            # Integration configuration schema
└── errors.py            # Integration-specific errors
```

**Deliverables:**
- [ ] `Integration` base class with standard interface
- [ ] Integration registration decorator
- [ ] Health check protocol
- [ ] Configuration validation
- [ ] Dependency resolution between integrations

### P5.2: Unified Configuration System
**Enhancement to:** `skeletonkey/core/config.py`

```toml
# New configuration sections
[integrations]
# Global integration settings
auto_discover = true
discover_paths = ["~/.skeletonkey/integrations", "/usr/local/share/skeletonkey/integrations"]
health_check_interval_s = 60

[integrations.stripe]
enabled = true
api_key = "{{PUB.stripe_api_key}}"
webhook_secret = "{{PUB.stripe_webhook_secret}}"

[integrations.postgres]
enabled = true
connection_string = "{{PUB.db_url}}"
pool_size = 10

[integrations.langchain]
enabled = true
api_base = "https://api.langchain.com"
```

**Deliverables:**
- [ ] Nested integration configuration
- [ ] Secret management integration (via `pub.*`)
- [ ] Environment variable overrides
- [ ] Configuration validation per integration

### P5.3: Integration Health & Status
**New Tool Group:** `integrations.*`

| Tool | Description |
|------|-------------|
| `integrations.list` | List all available integrations |
| `integrations.describe` | Get integration details and capabilities |
| `integrations.health` | Check integration health status |
| `integrations.enable` | Enable a disabled integration |
| `integrations.disable` | Disable an enabled integration |
| `integrations.test` | Test an integration connection |
| `integrations.config` | Get/set integration configuration |

### P5.4: Integration Sandboxing
**Enhancement to:** `skeletonkey/fsx/sandbox.py`

- [ ] Per-integration filesystem isolation
- [ ] Network access controls per integration
- [ ] Resource limits (memory, CPU, timeouts)
- [ ] Capability gating per integration

### P5.5: Integration Tool Manifest Standard
**New Schema:** `schemas/integration-manifest.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"},
    "version": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"},
    "name": {"type": "string"},
    "description": {"type": "string"},
    "author": {"type": "string"},
    "license": {"type": "string"},
    "homepage": {"type": "string", "format": "uri"},
    "category": {"type": "string", "enum": ["sdk", "api", "database", "framework", "toolchain", "cli", "runtime", "protocol", "codegen", "devkit", "3d", "billing", "agent"]},
    "capabilities": {
      "type": "array",
      "items": {"type": "string"}
    },
    "requirements": {
      "type": "array",
      "items": {"type": "string"}
    },
    "optional_dependencies": {
      "type": "array",
      "items": {"type": "string"}
    },
    "configuration_schema": {"type": "object"},
    "tools": {
      "type": "array",
      "items": {"$ref": "#/definitions/tool"}
    },
    "resources": {
      "type": "array",
      "items": {"$ref": "#/definitions/resource"}
    },
    "prompts": {
      "type": "array",
      "items": {"$ref": "#/definitions/prompt"}
    }
  },
  "required": ["id", "version", "name", "category"]
}
```

### P5.6: Integration Packaging & Distribution
**New System:** Integration Packs

```
integrations/
├── stripe/
│   ├── INTEGRATION.md      # Metadata and documentation
│   ├── integration.toml     # Configuration and manifest
│   ├── tools.py             # Tool implementations
│   └── tests/               # Integration tests
├── postgres/
│   ├── INTEGRATION.md
│   ├── integration.toml
│   └── tools.py
└── langchain/
    ├── INTEGRATION.md
    ├── integration.toml
    └── tools.py
```

**Deliverables:**
- [ ] Integration pack format specification
- [ ] Pack discovery and loading
- [ ] Pack versioning and updates
- [ ] Pack dependency resolution

### P5.7: Documentation & Examples
- [ ] Integration development guide
- [ ] Example integration implementations (2-3)
- [ ] Testing guidelines for integrations
- [ ] Packaging and distribution guide

---

## Phase P6: SDK & API Framework (2026-11 to 2026-12)

**Goal:** First-class support for SDKs, client libraries, and external API integrations.

### P6.0: REST API Integration Framework
**New Module:** `skeletonkey/integrations/rest/`

```python
# REST API client with automatic tool generation
from skeletonkey.integrations.rest import RestIntegration

class GitHubIntegration(RestIntegration):
    name = "github"
    base_url = "https://api.github.com"
    
    @RestIntegration.tool(
        method="GET",
        path="/repos/{owner}/{repo}",
        name="github.get_repo",
        description="Get repository information"
    )
    def get_repo(self, owner: str, repo: str):
        return {"owner": owner, "repo": repo}
    
    @RestIntegration.tool(
        method="POST",
        path="/repos/{owner}/{repo}/issues",
        name="github.create_issue",
        description="Create a new issue"
    )
    def create_issue(self, owner: str, repo: str, title: str, body: str):
        return {"owner": owner, "repo": repo, "title": title, "body": body}
```

**Deliverables:**
- [ ] `RestIntegration` base class
- [ ] Automatic tool generation from OpenAPI specs
- [ ] Authentication handling (API keys, OAuth, JWT)
- [ ] Rate limiting and retry logic
- [ ] Pagination support
- [ ] Error handling and normalization

### P6.1: GraphQL API Integration
**New Module:** `skeletonkey/integrations/graphql/`

```python
from skeletonkey.integrations.graphql import GraphQLIntegration

class GitHubGraphQLIntegration(GraphQLIntegration):
    name = "github_graphql"
    endpoint = "https://api.github.com/graphql"
    
    @GraphQLIntegration.query(
        name="github.graphql.query",
        description="Execute a GraphQL query"
    )
    def query(self, query: str, variables: dict = None):
        return {"query": query, "variables": variables}
```

**Deliverables:**
- [ ] `GraphQLIntegration` base class
- [ ] Query and mutation support
- [ ] Automatic schema introspection
- [ ] Type generation from schema
- [ ] Fragment support

### P6.2: gRPC API Integration
**New Module:** `skeletonkey/integrations/grpc/`

```python
from skeletonkey.integrations.grpc import GRPCIntegration

class ProtobufIntegration(GRPCIntegration):
    name = "grpc_example"
    proto_path = "path/to/service.proto"
    
    @GRPCIntegration.method(
        service="MyService",
        method="MyMethod",
        name="grpc.my_method",
        description="Call a gRPC method"
    )
    def my_method(self, request: dict):
        return request
```

**Deliverables:**
- [ ] `GRPCIntegration` base class
- [ ] Protobuf file loading and parsing
- [ ] Channel management
- [ ] Streaming support
- [ ] Error handling

### P6.3: WebSocket API Integration
**New Module:** `skeletonkey/integrations/websocket/`

```python
from skeletonkey.integrations.websocket import WebSocketIntegration

class RealtimeIntegration(WebSocketIntegration):
    name = "realtime"
    url = "wss://realtime.example.com"
    
    @WebSocketIntegration.subscribe(
        channel="updates",
        name="realtime.subscribe_updates",
        description="Subscribe to update events"
    )
    async def on_update(self, data: dict):
        # Handle incoming message
        return data
    
    @WebSocketIntegration.publish(
        channel="commands",
        name="realtime.publish_command",
        description="Publish a command"
    )
    async def publish_command(self, command: str):
        return {"command": command}
```

**Deliverables:**
- [ ] `WebSocketIntegration` base class
- [ ] Connection management
- [ ] Message serialization/deserialization
- [ ] Subscribe/publish pattern
- [ ] Reconnection logic

### P6.4: SDK Wrapper Framework
**New Module:** `skeletonkey/integrations/sdk/`

```python
from skeletonkey.integrations.sdk import SDKIntegration

class StripeSDKIntegration(SDKIntegration):
    name = "stripe"
    sdk_module = "stripe"
    
    def __init__(self):
        import stripe
        self.stripe = stripe
    
    @SDKIntegration.tool(
        name="stripe.create_customer",
        description="Create a Stripe customer"
    )
    def create_customer(self, email: str, name: str = None):
        return self.stripe.Customer.create(email=email, name=name)
    
    @SDKIntegration.tool(
        name="stripe.create_payment_intent",
        description="Create a payment intent"
    )
    def create_payment_intent(self, amount: int, currency: str = "usd"):
        return self.stripe.PaymentIntent.create(
            amount=amount,
            currency=currency
        )
```

**Deliverables:**
- [ ] `SDKIntegration` base class
- [ ] SDK initialization patterns
- [ ] Error normalization from SDK exceptions
- [ ] Type conversion between SDK and SkeletonKey
- [ ] Resource cleanup

### P6.5: API Generator (OpenAPI → Tools)
**New Module:** `skeletonkey/integrations/generators/openapi/`

```python
from skeletonkey.integrations.generators.openapi import OpenAPIGenerator

# Generate tools from an OpenAPI spec
generator = OpenAPIGenerator(
    spec_url="https://api.example.com/openapi.json",
    base_url="https://api.example.com",
    auth={
        "type": "api_key",
        "header": "X-API-Key",
        "secret_id": "api_key"
    }
)

# Generate all tools from the spec
tools = generator.generate_all()

# Or generate specific operations
tools = generator.generate_operations(["getUser", "createUser"])
```

**Deliverables:**
- [ ] OpenAPI 3.0/3.1 parser
- [ ] Operation → Tool manifest conversion
- [ ] Schema → Input schema conversion
- [ ] Authentication configuration
- [ ] Tool naming conventions
- [ ] Tag-based tool grouping

### P6.6: API Generator (Protobuf → Tools)
**New Module:** `skeletonkey/integrations/generators/protobuf/`

```python
from skeletonkey.integrations.generators.protobuf import ProtobufGenerator

# Generate tools from protobuf
generator = ProtobufGenerator(
    proto_file="service.proto",
    package="my.service"
)

# Generate all RPC methods as tools
tools = generator.generate_all()
```

**Deliverables:**
- [ ] Protobuf parser
- [ ] Service/Method → Tool manifest conversion
- [ ] Message type → Input/Output schema conversion
- [ ] Streaming RPC support

### P6.7: SDK & API Tool Group
**New Tools:** `sdk.*` and `api.*`

| Tool | Description |
|------|-------------|
| `sdk.list` | List available SDK integrations |
| `sdk.describe` | Describe an SDK's capabilities |
| `sdk.call` | Call an SDK method directly |
| `api.list` | List available API integrations |
| `api.describe` | Describe an API's endpoints |
| `api.generate` | Generate tools from an API spec |
| `api.test` | Test an API connection |

---

## Phase P7: Protocol Bindings (2026-12 to 2027-01)

**Goal:** Support for multiple protocols beyond MCP, enabling SkeletonKey to act as a universal protocol bridge.

### P7.0: Protocol Abstraction Layer
**New Module:** `skeletonkey/protocols/`

```
skeletonkey/protocols/
├── __init__.py          # Protocol registry
├── base.py              # Base protocol classes
├── jsonrpc.py           # JSON-RPC 2.0 support
├── grpc.py              # gRPC protocol support
├── websocket.py         # WebSocket protocol support
├── sse.py               # Server-Sent Events support
├── rest.py              # REST protocol support
└── mcp.py               # MCP protocol (existing, refactored)
```

**Deliverables:**
- [ ] `Protocol` base class
- [ ] `ProtocolServer` base class
- [ ] `ProtocolClient` base class
- [ ] Protocol registration system
- [ ] Protocol capability detection

### P7.1: JSON-RPC 2.0 Support
**New Module:** `skeletonkey/protocols/jsonrpc.py`

```python
from skeletonkey.protocols.jsonrpc import JSONRPCProtocol

# Server
server = JSONRPCProtocol(toolkit)
server.add_method("my_method", my_handler)
server.serve("0.0.0.0", 8080)

# Client
client = JSONRPCProtocol.client("http://localhost:8080")
result = client.call("my_method", args={"key": "value"})
```

**Deliverables:**
- [ ] JSON-RPC 2.0 server
- [ ] JSON-RPC 2.0 client
- [ ] Batch request support
- [ ] Notification support
- [ ] Error handling per JSON-RPC spec

### P7.2: gRPC Protocol Support
**New Module:** `skeletonkey/protocols/grpc.py`

```python
from skeletonkey.protocols.grpc import GRPCProtocol

# Server
server = GRPCProtocol(toolkit, proto_file="service.proto")
server.add_service(MyServiceServicer())
server.serve("0.0.0.0:50051")

# Client
client = GRPCProtocol.client("localhost:50051", proto_file="service.proto")
result = client.call("my_service.MyMethod", request={"key": "value"})
```

**Deliverables:**
- [ ] gRPC server with SkeletonKey toolkit
- [ ] gRPC client
- [ ] Protobuf code generation
- [ ] Streaming support
- [ ] Interceptor support

### P7.3: WebSocket Protocol Support
**New Module:** `skeletonkey/protocols/websocket.py`

```python
from skeletonkey.protocols.websocket import WebSocketProtocol

# Server
server = WebSocketProtocol(toolkit)
server.on("message", message_handler)
server.on("connect", connect_handler)
server.on("disconnect", disconnect_handler)
server.serve("0.0.0.0", 8080)

# Client
client = WebSocketProtocol.client("ws://localhost:8080")
client.send({"type": "call", "tool": "my_tool", "args": {}})
```

**Deliverables:**
- [ ] WebSocket server
- [ ] WebSocket client
- [ ] Message framing
- [ ] Connection management
- [ ] Broadcast support

### P7.4: Server-Sent Events (SSE) Support
**New Module:** `skeletonkey/protocols/sse.py`

```python
from skeletonkey.protocols.sse import SSEProtocol

# Server
server = SSEProtocol(toolkit)
server.add_event_source("updates", update_generator)
server.serve("0.0.0.0", 8080)

# Client
client = SSEProtocol.client("http://localhost:8080")
for event in client.subscribe("updates"):
    print(event.data)
```

**Deliverables:**
- [ ] SSE server
- [ ] SSE client
- [ ] Event streaming
- [ ] Reconnection support

### P7.5: REST Protocol Support
**New Module:** `skeletonkey/protocols/rest.py`

```python
from skeletonkey.protocols.rest import RESTProtocol

# Server
server = RESTProtocol(toolkit)
server.add_route("GET", "/api/tools", list_tools_handler)
server.add_route("POST", "/api/tools/{tool}", call_tool_handler)
server.serve("0.0.0.0", 8080)

# Client
client = RESTProtocol.client("http://localhost:8080")
result = client.get("/api/tools")
result = client.post("/api/tools/my_tool", json={"args": {}})
```

**Deliverables:**
- [ ] REST server
- [ ] REST client
- [ ] Route registration
- [ ] Middleware support
- [ ] Request/response normalization

### P7.6: Protocol Bridge
**New Module:** `skeletonkey/protocols/bridge.py`

```python
from skeletonkey.protocols.bridge import ProtocolBridge

# Bridge between MCP and REST
bridge = ProtocolBridge(
    source_protocol="mcp",
    source_config={"command": "python", "args": ["-m", "skeletonkey.mcp"]},
    target_protocol="rest",
    target_config={"port": 8080}
)
bridge.start()

# Now REST clients can access MCP tools
# GET /tools -> list MCP tools
# POST /tools/{tool} -> call MCP tool
```

**Deliverables:**
- [ ] Protocol bridge framework
- [ ] MCP ↔ REST bridge
- [ ] MCP ↔ JSON-RPC bridge
- [ ] MCP ↔ WebSocket bridge
- [ ] Bidirectional bridging

### P7.7: Protocol Tool Group
**New Tools:** `protocol.*`

| Tool | Description |
|------|-------------|
| `protocol.list` | List supported protocols |
| `protocol.describe` | Describe a protocol's capabilities |
| `protocol.serve` | Start a protocol server |
| `protocol.connect` | Connect to a protocol server |
| `protocol.bridge` | Create a protocol bridge |

---

## Phase P8: Codegen Systems (2027-01 to 2027-02)

**Goal:** Automatic generation of tools, manifests, and client code from various specifications.

### P8.0: Codegen Framework
**New Module:** `skeletonkey/codegen/`

```
skeletonkey/codegen/
├── __init__.py          # Codegen registry
├── base.py              # Base codegen classes
├── templates/           # Code templates
│   ├── python/          # Python code templates
│   ├── typescript/      # TypeScript code templates
│   └── go/              # Go code templates
├── openapi/             # OpenAPI codegen
├── protobuf/            # Protobuf codegen
├── json_schema/         # JSON Schema codegen
├── python_sdk/          # Python SDK codegen
└── manifest/            # Manifest generation
```

**Deliverables:**
- [ ] `CodeGenerator` base class
- [ ] Template engine
- [ ] Output formatting
- [ ] Validation
- [ ] Preview mode

### P8.1: Manifest Generator
**New Module:** `skeletonkey/codegen/manifest/`

```python
from skeletonkey.codegen.manifest import ManifestGenerator

# Generate a tool manifest from a Python function
generator = ManifestGenerator()

@generator.tool(
    name="my_tool",
    description="My awesome tool",
    risk="read"
)
def my_tool(arg1: str, arg2: int = 10):
    """Do something awesome."""
    return {"result": arg1 * arg2}

# Get the generated manifest
manifest = generator.generate(my_tool)
print(manifest.to_dict())
```

**Deliverables:**
- [ ] Function → Manifest conversion
- [ ] Type hint extraction
- [ ] Docstring parsing
- [ ] Argument validation
- [ ] Default value handling

### P8.2: OpenAPI Codegen
**New Module:** `skeletonkey/codegen/openapi/`

```python
from skeletonkey.codegen.openapi import OpenAPICodegen

# Generate Python client from OpenAPI spec
codegen = OpenAPICodegen(
    spec="https://api.example.com/openapi.json",
    output_dir="./generated",
    package_name="example_client",
    language="python"
)

# Generate client code
codegen.generate_client()

# Generate server stubs
codegen.generate_server()

# Generate SkeletonKey tools
codegen.generate_tools()
```

**Deliverables:**
- [ ] OpenAPI → Python client
- [ ] OpenAPI → TypeScript client
- [ ] OpenAPI → Go client
- [ ] OpenAPI → SkeletonKey tools
- [ ] OpenAPI → Server stubs

### P8.3: Protobuf Codegen
**New Module:** `skeletonkey/codegen/protobuf/`

```python
from skeletonkey.codegen.protobuf import ProtobufCodegen

# Generate Python code from protobuf
codegen = ProtobufCodegen(
    proto_file="service.proto",
    output_dir="./generated",
    language="python"
)

# Generate client code
codegen.generate_client()

# Generate server code
codegen.generate_server()

# Generate SkeletonKey tools
codegen.generate_tools()
```

**Deliverables:**
- [ ] Protobuf → Python client/server
- [ ] Protobuf → TypeScript client/server
- [ ] Protobuf → Go client/server
- [ ] Protobuf → SkeletonKey tools

### P8.4: JSON Schema Codegen
**New Module:** `skeletonkey/codegen/json_schema/`

```python
from skeletonkey.codegen.json_schema import JSONSchemaCodegen

# Generate Python classes from JSON Schema
codegen = JSONSchemaCodegen(
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"}
        }
    },
    class_name="Person",
    output_file="person.py"
)

# Generate the class
codegen.generate()

# Generate validation code
codegen.generate_validator()

# Generate SkeletonKey tool manifest
codegen.generate_manifest()
```

**Deliverables:**
- [ ] JSON Schema → Python classes
- [ ] JSON Schema → TypeScript interfaces
- [ ] JSON Schema → Go structs
- [ ] JSON Schema → Validation code
- [ ] JSON Schema → Tool manifests

### P8.5: Python SDK Codegen
**New Module:** `skeletonkey/codegen/python_sdk/`

```python
from skeletonkey.codegen.python_sdk import PythonSDKCodegen

# Generate SkeletonKey integration from Python SDK
codegen = PythonSDKCodegen(
    module="stripe",
    output_dir="./integrations/stripe",
    include=["Customer", "PaymentIntent", "Charge"],
    exclude=["internal"]
)

# Generate the integration
codegen.generate()
```

**Deliverables:**
- [ ] SDK introspection
- [ ] Method → Tool conversion
- [ ] Type conversion
- [ ] Error handling generation
- [ ] Documentation generation

### P8.6: Codegen Tool Group
**New Tools:** `codegen.*`

| Tool | Description |
|------|-------------|
| `codegen.list` | List available codegen templates |
| `codegen.generate` | Generate code from a specification |
| `codegen.preview` | Preview generated code without writing |
| `codegen.validate` | Validate a specification |
| `codegen.template` | Create a custom codegen template |

---

## Phase P9: Platform DevKits (2027-02 to 2027-04)

**Goal:** First-class support for major cloud platforms and services.

### P9.0: Cloud Platform Abstraction
**New Module:** `skeletonkey/platforms/`

```
skeletonkey/platforms/
├── __init__.py          # Platform registry
├── base.py              # Base platform classes
├── aws/
│   ├── __init__.py
│   ├── ec2.py
│   ├── s3.py
│   ├── lambda.py
│   └── ...
├── gcp/
│   ├── __init__.py
│   ├── compute.py
│   ├── storage.py
│   └── ...
├── azure/
│   ├── __init__.py
│   ├── vm.py
│   ├── blob.py
│   └── ...
└── multi/
    └── abstractions.py   # Multi-cloud abstractions
```

**Deliverables:**
- [ ] `Platform` base class
- [ ] Platform authentication management
- [ ] Resource naming conventions
- [ ] Error normalization
- [ ] Rate limiting

### P9.1: AWS DevKit
**New Integration:** `skeletonkey/platforms/aws/`

```python
from skeletonkey.platforms.aws import AWSPlatform

# Initialize with credentials from pub store
aws = AWSPlatform(
    region="us-east-1",
    credentials={
        "access_key_id": "{{PUB.aws_access_key_id}}",
        "secret_access_key": "{{PUB.aws_secret_access_key}}"
    }
)

# EC2 tools
aws.ec2.list_instances()
aws.ec2.start_instance(instance_id="i-1234567890")
aws.ec2.stop_instance(instance_id="i-1234567890")

# S3 tools
aws.s3.list_buckets()
aws.s3.list_objects(bucket="my-bucket")
aws.s3.get_object(bucket="my-bucket", key="my-file.txt")
aws.s3.put_object(bucket="my-bucket", key="my-file.txt", body=b"content")

# Lambda tools
aws.lambda.list_functions()
aws.lambda.invoke(function_name="my-function", payload={"key": "value"})
```

**Deliverables:**
- [ ] AWS authentication (IAM, STS, credentials)
- [ ] EC2 integration (instances, AMIs, security groups)
- [ ] S3 integration (buckets, objects, presigned URLs)
- [ ] Lambda integration (functions, invocations)
- [ ] DynamoDB integration (tables, items)
- [ ] SQS/SNS integration (queues, topics)
- [ ] CloudWatch integration (logs, metrics)
- [ ] Error handling and retry logic

### P9.2: Google Cloud DevKit
**New Integration:** `skeletonkey/platforms/gcp/`

```python
from skeletonkey.platforms.gcp import GCPPlatform

# Initialize with application default credentials
gcp = GCPPlatform(project_id="my-project")

# Compute Engine tools
gcp.compute.list_instances()
gcp.compute.start_instance(instance="my-instance", zone="us-central1-a")

# Cloud Storage tools
gcp.storage.list_buckets()
gcp.storage.list_objects(bucket="my-bucket")
gcp.storage.get_object(bucket="my-bucket", object="my-file.txt")

# Cloud Functions tools
gcp.functions.list_functions()
gcp.functions.call(function="my-function", data={"key": "value"})
```

**Deliverables:**
- [ ] GCP authentication (service accounts, ADC)
- [ ] Compute Engine integration
- [ ] Cloud Storage integration
- [ ] Cloud Functions integration
- [ ] Firestore integration
- [ ] Pub/Sub integration
- [ ] BigQuery integration

### P9.3: Azure DevKit
**New Integration:** `skeletonkey/platforms/azure/`

```python
from skeletonkey.platforms.azure import AzurePlatform

# Initialize with credentials
azure = AzurePlatform(
    tenant_id="{{PUB.azure_tenant_id}}",
    client_id="{{PUB.azure_client_id}}",
    client_secret="{{PUB.azure_client_secret}}",
    subscription_id="{{PUB.azure_subscription_id}}"
)

# VM tools
azure.vm.list_vms()
azure.vm.start_vm(resource_group="my-rg", vm_name="my-vm")

# Blob Storage tools
azure.blob.list_containers()
azure.blob.list_blobs(container="my-container")
azure.blob.get_blob(container="my-container", blob="my-file.txt")

# Functions tools
azure.functions.list_functions()
azure.functions.invoke(function="my-function", data={"key": "value"})
```

**Deliverables:**
- [ ] Azure authentication (service principals, managed identity)
- [ ] VM integration
- [ ] Blob Storage integration
- [ ] Functions integration
- [ ] Cosmos DB integration
- [ ] Event Grid integration
- [ ] Key Vault integration

### P9.4: Vercel DevKit
**New Integration:** `skeletonkey/platforms/vercel/`

```python
from skeletonkey.platforms.vercel import VercelPlatform

# Initialize with token
vercel = VercelPlatform(token="{{PUB.vercel_token}}")

# Project tools
vercel.projects.list()
vercel.projects.get("my-project")

# Deployment tools
vercel.deployments.list(project="my-project")
vercel.deployments.create(project="my-project", ref="main")

# Edge Functions tools
vercel.functions.list()
vercel.functions.invoke(function="my-function", data={"key": "value"})
```

**Deliverables:**
- [ ] Vercel authentication (tokens)
- [ ] Project management
- [ ] Deployment management
- [ ] Edge Functions integration
- [ ] Storage integration
- [ ] Edge Config integration

### P9.5: Supabase DevKit
**New Integration:** `skeletonkey/platforms/supabase/`

```python
from skeletonkey.platforms.supabase import SupabasePlatform

# Initialize with URL and key
supabase = SupabasePlatform(
    url="https://my-project.supabase.co",
    key="{{PUB.supabase_key}}"
)

# Database tools (PostgREST)
supabase.db.query("posts").select("*").execute()
supabase.db.query("posts").insert({"title": "Hello"}).execute()

# Auth tools
supabase.auth.sign_up(email="user@example.com", password="password")
supabase.auth.sign_in(email="user@example.com", password="password")

# Storage tools
supabase.storage.list_buckets()
supabase.storage.upload(bucket="my-bucket", path="my-file.txt", body=b"content")

# Realtime tools
supabase.realtime.subscribe("posts").on("INSERT", lambda: print("New post!"))
```

**Deliverables:**
- [ ] Supabase authentication (JWT, API keys)
- [ ] Database integration (PostgREST)
- [ ] Auth integration
- [ ] Storage integration
- [ ] Realtime integration
- [ ] Edge Functions integration

### P9.6: Firebase DevKit
**New Integration:** `skeletonkey/platforms/firebase/`

```python
from skeletonkey.platforms.firebase import FirebasePlatform

# Initialize with credentials
firebase = FirebasePlatform(
    project_id="my-project",
    credentials={"type": "service_account", ...}
)

# Firestore tools
firebase.firestore.list_collections()
firebase.firestore.get_doc("users", "user123")
firebase.firestore.set_doc("users", "user123", {"name": "John"})

# Auth tools
firebase.auth.list_users()
firebase.auth.create_user(email="user@example.com", password="password")

# Storage tools
firebase.storage.list_buckets()
firebase.storage.upload(bucket="my-bucket", path="my-file.txt", body=b"content")
```

**Deliverables:**
- [ ] Firebase authentication (service accounts, custom tokens)
- [ ] Firestore integration
- [ ] Auth integration
- [ ] Storage integration
- [ ] Realtime Database integration
- [ ] Cloud Functions integration

### P9.7: Platform Tool Group
**New Tools:** `platform.*`

| Tool | Description |
|------|-------------|
| `platform.list` | List available platform integrations |
| `platform.describe` | Describe a platform's capabilities |
| `platform.auth` | Authenticate with a platform |
| `platform.resources` | List resources on a platform |
| `platform.execute` | Execute a platform-specific operation |

---

## Phase P10: Domain Integrations (2027-04 to 2027-06)

**Goal:** Deep integrations for specific domains: 3D, billing, databases, agents.

### P10.0: Integration Category System
**Enhancement to:** `skeletonkey/integrations/`

```python
# Integration categories
CATEGORIES = {
    "3d": {
        "name": "3D Modeling & Rendering",
        "description": "3D modeling, rendering, and CAD integrations",
        "tags": ["3d", "modeling", "rendering", "cad", "blender", "threejs"]
    },
    "billing": {
        "name": "Billing & Payments",
        "description": "Payment processing, subscriptions, invoicing",
        "tags": ["billing", "payment", "stripe", "paddle", "subscription"]
    },
    "database": {
        "name": "Databases",
        "description": "Database connectivity and query tools",
        "tags": ["database", "sql", "nosql", "postgres", "mongodb"]
    },
    "agent": {
        "name": "Agent Harnesses",
        "description": "Integration with AI agent frameworks",
        "tags": ["agent", "langchain", "llama-index", "crewai", "autogen"]
    }
}
```

### P10.1: 3D Modeling Integrations
**New Module:** `skeletonkey/domains/threed/`

#### P10.1.1: Blender Integration
```python
from skeletonkey.domains.threed.blender import BlenderIntegration

blender = BlenderIntegration(
    executable="/usr/bin/blender",
    temp_dir="/tmp/blender"
)

# Scene tools
blender.scene.new()
blender.scene.open(path="my-scene.blend")
blender.scene.save(path="my-scene.blend")

# Object tools
blender.object.create(type="cube", name="my-cube")
blender.object.create(type="sphere", name="my-sphere", radius=2)
blender.object.delete(name="my-cube")

# Render tools
blender.render.image(
    scene="my-scene",
    output="render.png",
    resolution=(1920, 1080),
    samples=128
)

# Animation tools
blender.animation.render(
    scene="my-scene",
    output="animation.mp4",
    start_frame=1,
    end_frame=250,
    fps=30
)
```

#### P10.1.2: Three.js Integration
```python
from skeletonkey.domains.threed.threejs import ThreeJSIntegration

threejs = ThreeJSIntegration()

# Generate Three.js code
code = threejs.generate_scene(
    objects=[
        {"type": "box", "width": 1, "height": 1, "depth": 1},
        {"type": "sphere", "radius": 0.5, "position": [2, 0, 0]}
    ],
    lights=[{"type": "ambient", "intensity": 0.5}],
    camera={"position": [5, 5, 5], "lookAt": [0, 0, 0]}
)

# Render to image (via headless browser)
image = threejs.render_to_image(code, width=800, height=600)

# Export to GLTF
threejs.export_to_gltf(code, path="scene.gltf")
```

#### P10.1.3: CAD Integration (OpenSCAD, FreeCAD)
```python
from skeletonkey.domains.threed.cad import OpenSCADIntegration

openscad = OpenSCADIntegration(executable="openscad")

# Generate CAD model
code = """
module param() {
    height = 10;
    width = 20;
    depth = 30;
}

cube(size=[param.width, param.depth, param.height]);
"""

# Render to STL
openscad.render_to_stl(code, path="model.stl")

# Render to PNG
openscad.render_to_png(code, path="preview.png", width=800, height=600)
```

**Deliverables:**
- [ ] Blender integration (scene, object, render, animation)
- [ ] Three.js integration (code generation, rendering)
- [ ] OpenSCAD integration (code generation, rendering)
- [ ] FreeCAD integration (scripting, export)
- [ ] GLTF/STL/OBJ format support
- [ ] 3D preview in Live Panel

### P10.2: Billing & Payment Integrations
**New Module:** `skeletonkey/domains/billing/`

#### P10.2.1: Stripe Integration
```python
from skeletonkey.domains.billing.stripe import StripeBilling

stripe = StripeBilling(api_key="{{PUB.stripe_api_key}}")

# Customer tools
stripe.customers.create(email="user@example.com", name="John Doe")
stripe.customers.list(limit=100)
stripe.customers.get("cus_123")
stripe.customers.update("cus_123", name="Jane Doe")
stripe.customers.delete("cus_123")

# Payment Intent tools
stripe.payment_intents.create(
    amount=1000,  # $10.00
    currency="usd",
    customer="cus_123",
    payment_method_types=["card"]
)
stripe.payment_intents.retrieve("pi_123")
stripe.payment_intents.confirm("pi_123")
stripe.payment_intents.cancel("pi_123")

# Subscription tools
stripe.subscriptions.create(
    customer="cus_123",
    items=[{"price": "price_123", "quantity": 1}],
    payment_behavior="default_incomplete"
)
stripe.subscriptions.list(customer="cus_123")
stripe.subscriptions.cancel("sub_123")

# Invoice tools
stripe.invoices.create(customer="cus_123", auto_advance=True)
stripe.invoices.list(customer="cus_123")
stripe.invoices.retrieve("in_123")
stripe.invoices.pay("in_123")

# Webhook tools
stripe.webhooks.verify(payload, signature, secret="{{PUB.stripe_webhook_secret}}")
```

#### P10.2.2: Paddle Integration
```python
from skeletonkey.domains.billing.paddle import PaddleBilling

paddle = PaddleBilling(
    api_key="{{PUB.paddle_api_key}}",
    vendor_id="12345"
)

# Product tools
paddle.products.create(name="My Product", price=1000)
paddle.products.list()
paddle.products.get("prod_123")

# Subscription tools
paddle.subscriptions.create(
    customer_email="user@example.com",
    product_id="prod_123",
    price_id="price_123"
)
paddle.subscriptions.list()
paddle.subscriptions.cancel("sub_123")

# Payment tools
paddle.payments.create(
    customer_email="user@example.com",
    items=[{"price_id": "price_123", "quantity": 1}],
    currency="USD"
)
```

#### P10.2.3: Lemon Squeezy Integration
```python
from skeletonkey.domains.billing.lemon_squeezy import LemonSqueezyBilling

lemon = LemonSqueezyBilling(api_key="{{PUB.lemon_squeezy_api_key}}")

# Product tools
lemon.products.create(name="My Product", price=1000)
lemon.products.list()

# Checkout tools
lemon.checkouts.create(
    product_id="prod_123",
    variant_id="var_123",
    customer_email="user@example.com",
    success_url="https://example.com/success",
    failure_url="https://example.com/failure"
)

# Subscription tools
lemon.subscriptions.list()
lemon.subscriptions.cancel("sub_123")
```

**Deliverables:**
- [ ] Stripe integration (customers, payments, subscriptions, invoices, webhooks)
- [ ] Paddle integration (products, subscriptions, payments)
- [ ] Lemon Squeezy integration (products, checkouts, subscriptions)
- [ ] Payment method management
- [ ] Subscription lifecycle management
- [ ] Webhook verification
- [ ] Billing analytics and reporting

### P10.3: Database Integrations
**New Module:** `skeletonkey/domains/database/`

#### P10.3.1: PostgreSQL Integration
```python
from skeletonkey.domains.database.postgres import PostgreSQLIntegration

pg = PostgreSQLIntegration(
    connection_string="{{PUB.db_url}}",
    pool_size=10
)

# Query tools
pg.query("SELECT * FROM users WHERE email = $1", ["user@example.com"])
pg.query_one("SELECT * FROM users WHERE id = $1", [123])
pg.execute("INSERT INTO users (name, email) VALUES ($1, $2)", ["John", "john@example.com"])

# Table tools
pg.tables.list()
pg.tables.describe("users")
pg.tables.create("users", columns={
    "id": "SERIAL PRIMARY KEY",
    "name": "TEXT",
    "email": "TEXT UNIQUE"
})
pg.tables.drop("users")

# Transaction tools
pg.transaction.begin()
pg.transaction.commit()
pg.transaction.rollback()

# Migration tools
pg.migrate.apply("migrations/001_create_users.sql")
pg.migrate.list()
```

#### P10.3.2: MongoDB Integration
```python
from skeletonkey.domains.database.mongodb import MongoDBIntegration

mongo = MongoDBIntegration(
    connection_string="{{PUB.mongo_url}}",
    database="my-database"
)

# Collection tools
mongo.collections.list()
mongo.collections.create("users")
mongo.collections.drop("users")

# Document tools
mongo.documents.insert("users", {"name": "John", "email": "john@example.com"})
mongo.documents.insert_many("users", [{"name": "Jane"}, {"name": "Bob"}])
mongo.documents.find("users", {"email": "john@example.com"})
mongo.documents.find_one("users", {"_id": "123"})
mongo.documents.update("users", {"_id": "123"}, {"$set": {"name": "Jane"}})
mongo.documents.delete("users", {"_id": "123"})
mongo.documents.delete_many("users", {"active": False})

# Aggregation tools
mongo.aggregate("users", [
    {"$match": {"active": True}},
    {"$group": {"_id": "$country", "count": {"$sum": 1}}}
])

# Index tools
mongo.indexes.create("users", "email", unique=True)
mongo.indexes.list("users")
mongo.indexes.drop("users", "email_1")
```

#### P10.3.2: SQLite Integration
```python
from skeletonkey.domains.database.sqlite import SQLiteIntegration

sqlite = SQLiteIntegration(database="my-database.db")

# Query tools (same interface as PostgreSQL)
sqlite.query("SELECT * FROM users")
sqlite.execute("INSERT INTO users (name) VALUES (?)", ["John"])

# In-memory database
memory_db = SQLiteIntegration(database=":memory:")
```

#### P10.3.3: Redis Integration
```python
from skeletonkey.domains.database.redis import RedisIntegration

redis = RedisIntegration(
    host="{{PUB.redis_host}}",
    port={{PUB.redis_port}},
    password="{{PUB.redis_password}}",
    db=0
)

# Key-value tools
redis.set("my-key", "my-value")
redis.get("my-key")
redis.delete("my-key")
redis.exists("my-key")
redis.expire("my-key", 60)  # 60 seconds

# Hash tools
redis.hset("user:123", "name", "John")
redis.hget("user:123", "name")
redis.hgetall("user:123")

# List tools
redis.lpush("my-list", "item1")
redis.rpush("my-list", "item2")
redis.lpop("my-list")
redis.rpop("my-list")
redis.lrange("my-list", 0, -1)

# Set tools
redis.sadd("my-set", "item1")
redis.srem("my-set", "item1")
redis.smembers("my-set")

# Pub/Sub tools
redis.publish("my-channel", "my-message")
redis.subscribe("my-channel")  # Returns subscription ID
redis.unsubscribe(subscription_id)
```

**Deliverables:**
- [ ] PostgreSQL integration (queries, transactions, migrations)
- [ ] MongoDB integration (documents, aggregations, indexes)
- [ ] SQLite integration (file-based, in-memory)
- [ ] Redis integration (key-value, hashes, lists, sets, pub/sub)
- [ ] Connection pooling
- [ ] Query result formatting
- [ ] ORM-like interface (optional)

### P10.4: Agent Harness Integrations
**New Module:** `skeletonkey/domains/agents/`

#### P10.4.1: LangChain Integration
```python
from skeletonkey.domains.agents.langchain import LangChainIntegration

langchain = LangChainIntegration(
    api_key="{{PUB.langchain_api_key}}",
    project_id="my-project"
)

# LLM tools
langchain.llms.list()
langchain.llms.invoke(
    model="gpt-4",
    prompt="Hello, how are you?",
    temperature=0.7,
    max_tokens=100
)
langchain.llms.stream(
    model="gpt-4",
    prompt="Tell me a story"
)  # Returns generator

# Chain tools
langchain.chains.create(
    name="my-chain",
    steps=[
        {"type": "llm", "model": "gpt-4", "prompt": "First step"},
        {"type": "llm", "model": "gpt-4", "prompt": "Second step"}
    ]
)
langchain.chains.invoke("my-chain", input={"key": "value"})

# Memory tools
langchain.memory.create(store="my-store", type="conversation")
langchain.memory.get("my-store", key="my-key")
langchain.memory.set("my-store", key="my-key", value="my-value")

# Document tools
langchain.documents.upload(
    file_path="document.pdf",
    store="my-store"
)
langchain.documents.search(
    store="my-store",
    query="What is the main topic?"
)
```

#### P10.4.2: LlamaIndex Integration
```python
from skeletonkey.domains.agents.llama_index import LlamaIndexIntegration

llamaindex = LlamaIndexIntegration(
    api_key="{{PUB.llamaindex_api_key}}"
)

# Index tools
llamaindex.indexes.create(
    name="my-index",
    documents=["doc1.txt", "doc2.txt"]
)
llamaindex.indexes.query(
    name="my-index",
    query="What is the main topic?"
)
llamaindex.indexes.list()
llamaindex.indexes.delete("my-index")

# Vector store tools
llamaindex.vector_stores.create(
    name="my-store",
    dimension=1536
)
llamaindex.vector_stores.insert(
    name="my-store",
    vectors=[{"id": "1", "vector": [0.1, 0.2, ...], "metadata": {"text": "hello"}}]
)
llamaindex.vector_stores.query(
    name="my-store",
    vector=[0.1, 0.2, ...],
    k=5
)
```

#### P10.4.3: CrewAI Integration
```python
from skeletonkey.domains.agents.crewai import CrewAIIntegration

crewai = CrewAIIntegration()

# Agent tools
crewai.agents.create(
    name="researcher",
    role="Researcher",
    goal="Find the best information",
    backstory="You are a world-class researcher",
    llm="gpt-4"
)
crewai.agents.list()
crewai.agents.get("researcher")

# Task tools
crewai.tasks.create(
    description="Research AI trends",
    agent="researcher",
    expected_output="A report on AI trends"
)
crewai.tasks.execute("task-123")

# Crew tools
crewai.crews.create(
    name="my-crew",
    agents=["researcher", "writer"],
    tasks=["task-123", "task-456"]
)
crewai.crews.execute("my-crew")
```

#### P10.4.4: AutoGen Integration
```python
from skeletonkey.domains.agents.autogen import AutoGenIntegration

autogen = AutoGenIntegration()

# Agent tools
autogen.agents.create(
    name="assistant",
    llm_config={"model": "gpt-4"},
    system_message="You are a helpful assistant"
)
autogen.agents.create(
    name="user_proxy",
    llm_config=None,
    human_input_mode="ALWAYS"
)

# Conversation tools
autogen.conversations.create(
    agents=["assistant", "user_proxy"],
    messages=[{"role": "user", "content": "Hello!"}]
)
result = autogen.conversations.start("conv-123")

# Group chat tools
autogen.groupchats.create(
    agents=["assistant1", "assistant2", "user_proxy"],
    messages=[{"role": "user", "content": "Solve this problem"}]
)
```

#### P10.4.5: Microsoft Semantic Kernel Integration
```python
from skeletonkey.domains.agents.semantic_kernel import SemanticKernelIntegration

sk = SemanticKernelIntegration(
    api_key="{{PUB.azure_openai_key}}",
    endpoint="{{PUB.azure_openai_endpoint}}"
)

# Kernel tools
sk.kernel.import_plugin("my-plugin", "my-plugin-dir")
sk.kernel.list_plugins()

# Function tools
sk.functions.create(
    name="my-function",
    prompt="You are a helpful assistant",
    plugin="my-plugin"
)
sk.functions.invoke("my-function", input="Hello")

# Memory tools
sk.memory.save("my-memory", "my-key", "my-value")
sk.memory.load("my-memory", "my-key")
```

**Deliverables:**
- [ ] LangChain integration (LLMs, chains, memory, documents)
- [ ] LlamaIndex integration (indexes, vector stores, retrieval)
- [ ] CrewAI integration (agents, tasks, crews)
- [ ] AutoGen integration (agents, conversations, group chats)
- [ ] Microsoft Semantic Kernel integration (kernels, functions, memory)
- [ ] Agent lifecycle management
- [ ] Multi-agent orchestration
- [ ] Memory and context management

### P10.5: Domain Tool Groups
**New Tools:** `threed.*`, `billing.*`, `database.*`, `agents.*`

| Category | Tools |
|----------|-------|
| 3D | `threed.render`, `threed.model`, `threed.export`, `threed.preview` |
| Billing | `billing.create_customer`, `billing.create_payment`, `billing.create_subscription` |
| Database | `database.query`, `database.execute`, `database.transaction` |
| Agents | `agents.create`, `agents.invoke`, `agents.converse`, `agents.orchestrate` |

---

## Phase P11: Ecosystem & Distribution (2027-06 to 2027-08)

**Goal:** Make SkeletonKey the de facto standard for AI tool integration with a thriving ecosystem.

### P11.0: Package Registry
**New Service:** `registry.skeletonkey.dev`

A public registry for SkeletonKey integrations, tools, and skills.

```bash
# Publish an integration
sk integration publish my-integration-1.0.0.tar.gz

# Install an integration
sk integration install stripe

# Search for integrations
sk integration search "billing"

# Update an integration
sk integration update stripe

# Remove an integration
sk integration remove stripe
```

**Deliverables:**
- [ ] Public integration registry
- [ ] Package format specification
- [ ] Versioning and dependency resolution
- [ ] Security scanning and signing
- [ ] CLI for package management
- [ ] Web interface for browsing

### P11.1: Integration Marketplace
**New UI:** Marketplace Web Application

A web-based marketplace for discovering and installing integrations.

**Features:**
- [ ] Browse integrations by category
- [ ] Search and filter
- [ ] Integration details page
- [ ] Installation with one click
- [ ] User reviews and ratings
- [ ] Usage statistics
- [ ] Documentation preview

### P11.2: Integration CLI
**New Command:** `sk integration`

```bash
# List installed integrations
sk integration list

# Show integration info
sk integration show stripe

# Install from registry
sk integration install stripe

# Install from local directory
sk integration install ./my-integration

# Install from Git repository
sk integration install git+https://github.com/user/my-integration.git

# Update integration
sk integration update stripe

# Remove integration
sk integration remove stripe

# Search registry
sk integration search "payment"

# Pack integration for distribution
sk integration pack ./my-integration

# Publish to registry
sk integration publish my-integration-1.0.0.tar.gz
```

### P11.3: Integration Testing Framework
**New Module:** `skeletonkey/testing/integrations/`

```python
from skeletonkey.testing.integrations import IntegrationTestCase

class StripeIntegrationTest(IntegrationTestCase):
    integration = "stripe"
    
    def test_create_customer(self):
        result = self.call("stripe.create_customer", {
            "email": "test@example.com"
        })
        self.assert_ok(result)
        self.assert_in("id", result.data)
        self.assert_starts_with("cus_", result.data["id"])
    
    def test_list_customers(self):
        # Create a test customer first
        self.call("stripe.create_customer", {"email": "test@example.com"})
        
        # List customers
        result = self.call("stripe.list_customers", {"limit": 10})
        self.assert_ok(result)
        self.assert_is_list(result.data["customers"])

# Run tests
if __name__ == "__main__":
    IntegrationTestCase.main()
```

**Deliverables:**
- [ ] Integration test base class
- [ ] Mocking utilities
- [ ] Test fixtures
- [ ] CI/CD integration
- [ ] Test coverage reporting

### P11.4: Integration Documentation System
**New Module:** `skeletonkey/docs/integrations/`

```python
from skeletonkey.docs.integrations import IntegrationDocGenerator

# Generate documentation for an integration
generator = IntegrationDocGenerator("stripe")
docs = generator.generate()

# Write to file
generator.write("docs/integrations/stripe.md")
```

**Deliverables:**
- [ ] Documentation generator
- [ ] API reference generation
- [ ] Example generation
- [ ] Tutorial generation
- [ ] Markdown/HTML output

### P11.5: Integration Analytics
**New Service:** Analytics Collection

Collect anonymized usage statistics to improve integrations.

```python
from skeletonkey.analytics import IntegrationAnalytics

analytics = IntegrationAnalytics()

# Track integration usage
analytics.track_integration_use("stripe", "create_customer")

# Track errors
analytics.track_error("stripe", "create_customer", "INVALID_ARGUMENT", "Invalid email")

# Get usage statistics
stats = analytics.get_usage_stats("stripe")
```

**Deliverables:**
- [ ] Usage tracking (opt-in)
- [ ] Error tracking
- [ ] Performance metrics
- [ ] Popularity rankings
- [ ] Privacy-preserving aggregation

### P11.6: Ecosystem Tool Group
**New Tools:** `ecosystem.*`

| Tool | Description |
|------|-------------|
| `ecosystem.search` | Search for integrations in the registry |
| `ecosystem.install` | Install an integration |
| `ecosystem.list` | List installed integrations |
| `ecosystem.update` | Update an integration |
| `ecosystem.remove` | Remove an integration |
| `ecosystem.publish` | Publish an integration |

---

## Implementation Tracking

### Current Status (2026-09-07)

| Component | Status | Notes |
|-----------|--------|-------|
| Project Indexer | ✅ Implemented | Basic scanning in place |
| Integration Registry | 🟡 In Progress | Core structure defined |
| REST Integration | ⏳ Not Started | Design complete |
| GraphQL Integration | ⏳ Not Started | Design complete |
| gRPC Integration | ⏳ Not Started | Design complete |
| WebSocket Integration | ⏳ Not Started | Design complete |
| SDK Wrapper | ⏳ Not Started | Design complete |
| OpenAPI Generator | ⏳ Not Started | Design complete |
| Protobuf Generator | ⏳ Not Started | Design complete |
| JSON Schema Generator | ⏳ Not Started | Design complete |
| AWS DevKit | ⏳ Not Started | Design complete |
| GCP DevKit | ⏳ Not Started | Design complete |
| Azure DevKit | ⏳ Not Started | Design complete |
| Vercel DevKit | ⏳ Not Started | Design complete |
| Supabase DevKit | ⏳ Not Started | Design complete |
| Firebase DevKit | ⏳ Not Started | Design complete |
| Blender Integration | ⏳ Not Started | Design complete |
| Three.js Integration | ⏳ Not Started | Design complete |
| CAD Integration | ⏳ Not Started | Design complete |
| Stripe Integration | ⏳ Not Started | Design complete |
| Paddle Integration | ⏳ Not Started | Design complete |
| Lemon Squeezy Integration | ⏳ Not Started | Design complete |
| PostgreSQL Integration | ⏳ Not Started | Design complete |
| MongoDB Integration | ⏳ Not Started | Design complete |
| SQLite Integration | ⏳ Not Started | Design complete |
| Redis Integration | ⏳ Not Started | Design complete |
| LangChain Integration | ⏳ Not Started | Design complete |
| LlamaIndex Integration | ⏳ Not Started | Design complete |
| CrewAI Integration | ⏳ Not Started | Design complete |
| AutoGen Integration | ⏳ Not Started | Design complete |
| Semantic Kernel Integration | ⏳ Not Started | Design complete |
| Package Registry | ⏳ Not Started | Design complete |
| Integration Marketplace | ⏳ Not Started | Design complete |
| Integration CLI | ⏳ Not Started | Design complete |
| Integration Testing | ⏳ Not Started | Design complete |
| Documentation System | ⏳ Not Started | Design complete |
| Analytics | ⏳ Not Started | Design complete |

---

## Risk Register

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| Scope creep | High | High | Strict phase boundaries, MVP first | All |
| Integration complexity | Medium | High | Start with simple integrations, iterate | Dev Team |
| Performance bottlenecks | Medium | Medium | Async operations, caching, connection pooling | Dev Team |
| Security vulnerabilities | Low | Very High | Sandboxing, input validation, secret management | Security Team |
| Dependency conflicts | Medium | Medium | Version pinning, isolation, testing | DevOps |
| Maintenance burden | High | Medium | Clear ownership, documentation, automation | All |
| Ecosystem fragmentation | Medium | High | Clear standards, registry, testing | Ecosystem Team |

---

## Non-Goals

1. **Replacing existing tools** - We integrate with existing SDKs and tools, not replace them
2. **Vendor lock-in** - All integrations are optional and pluggable
3. **Full feature parity** - We provide the 80% of features that cover 95% of use cases
4. **Real-time sync** - All operations are eventually consistent, not real-time
5. **GUI-first** - CLI and programmatic interfaces are first-class citizens
6. **Single language** - While we generate code for multiple languages, SkeletonKey itself remains Python-first

---

## Success Criteria

### Phase P5 (Integration Foundation)
- [ ] Project indexer scans and discovers integrations
- [ ] Integration registry tracks all integrations
- [ ] Health checks work for all integrations
- [ ] Configuration system supports nested integration configs
- [ ] Integration tool manifests are standardized
- [ ] Integration packs can be distributed and loaded

### Phase P6 (SDK & API Framework)
- [ ] REST API integration works with 3+ public APIs
- [ ] GraphQL API integration works with 2+ public APIs
- [ ] gRPC API integration works with 1+ service
- [ ] WebSocket API integration works with 1+ service
- [ ] SDK wrapper framework works with 2+ SDKs
- [ ] OpenAPI generator creates working tools from 3+ specs
- [ ] Protobuf generator creates working tools from 2+ specs
- [ ] JSON Schema generator creates working tools from 3+ schemas

### Phase P7 (Protocol Bindings)
- [ ] JSON-RPC 2.0 server and client work
- [ ] gRPC server and client work
- [ ] WebSocket server and client work
- [ ] SSE server and client work
- [ ] REST server and client work
- [ ] Protocol bridge works between 2+ protocol pairs
- [ ] All protocols support tool discovery and invocation

### Phase P8 (Codegen Systems)
- [ ] Manifest generator creates valid manifests from Python functions
- [ ] OpenAPI codegen creates working clients from 3+ specs
- [ ] Protobuf codegen creates working clients from 2+ specs
- [ ] JSON Schema codegen creates working classes from 3+ schemas
- [ ] Python SDK codegen creates working integrations from 2+ SDKs
- [ ] All codegen systems support preview mode

### Phase P9 (Platform DevKits)
- [ ] AWS DevKit works with 5+ services
- [ ] GCP DevKit works with 5+ services
- [ ] Azure DevKit works with 5+ services
- [ ] Vercel DevKit works with 3+ services
- [ ] Supabase DevKit works with 4+ services
- [ ] Firebase DevKit works with 4+ services
- [ ] All DevKits support authentication and error handling

### Phase P10 (Domain Integrations)
- [ ] 3D Modeling: 2+ integrations (Blender, Three.js)
- [ ] Billing: 3+ integrations (Stripe, Paddle, Lemon Squeezy)
- [ ] Databases: 4+ integrations (PostgreSQL, MongoDB, SQLite, Redis)
- [ ] Agents: 5+ integrations (LangChain, LlamaIndex, CrewAI, AutoGen, Semantic Kernel)
- [ ] All integrations have comprehensive tool coverage
- [ ] All integrations have tests and documentation

### Phase P11 (Ecosystem & Distribution)
- [ ] Package registry is live and operational
- [ ] 20+ integrations published to registry
- [ ] Integration CLI works for all operations
- [ ] Marketplace web app is live
- [ ] Testing framework used by 5+ integrations
- [ ] Documentation system generates docs for 5+ integrations
- [ ] Analytics system collects and displays usage data

---

## Contributing

### Adding a New Integration

1. **Design** - Create an ADR in `docs/adr/` for the integration
2. **Implement** - Create the integration module in `skeletonkey/integrations/` or `skeletonkey/domains/`
3. **Test** - Add tests in `tests/integrations/`
4. **Document** - Add documentation in `docs/integrations/`
5. **Package** - Create an integration pack
6. **Publish** - Publish to the registry (optional)

### Integration Checklist

- [ ] Follows the integration manifest standard
- [ ] Has comprehensive error handling
- [ ] Has input validation
- [ ] Has rate limiting (if applicable)
- [ ] Has authentication support (if applicable)
- [ ] Has health checks
- [ ] Has configuration options
- [ ] Has tests with >80% coverage
- [ ] Has documentation
- [ ] Has examples

---

## Maintenance

### Versioning

- **SkeletonKey Core**: Follows semantic versioning (MAJOR.MINOR.PATCH)
- **Integrations**: Follows semantic versioning, independent of core
- **Protocol Bindings**: Follows semantic versioning, independent of core
- **Codegen Systems**: Follows semantic versioning, independent of core

### Deprecation Policy

- **Integrations**: 6 months notice before removal
- **APIs**: 12 months notice before breaking changes
- **Tools**: 6 months notice before removal
- **Configurations**: 12 months notice before breaking changes

### Support Policy

| Component | Support Level | Lifecycle |
|-----------|---------------|-----------|
| Core Toolkit | Full | Active development |
| MCP Protocol | Full | Active development |
| Integrations | Best-effort | Maintained by community |
| Platform DevKits | Best-effort | Maintained by community |
| Codegen Systems | Full | Active development |

---

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2026-09-07 | Initial expanded plan created | Arena AI |
| 2026-09-07 | Phase P5 started (Integration Foundation) | Arena AI |

---

## References

1. [Original PLAN.md](../PLAN.md) - The original phase plan
2. [TOOL-CONTRACT.md](../docs/TOOL-CONTRACT.md) - Tool contract specification
3. [SECURITY-MODEL.md](../docs/SECURITY-MODEL.md) - Security model
4. [SKILLS-SPEC.md](../docs/SKILLS-SPEC.md) - Skill specification
5. [MCP Specification](https://github.com/modelcontextprotocol/specification) - MCP protocol

---

## Appendix A: Integration Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SkeletonKey Core                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────────┐  │
│  │   Registry   │  │   Engine     │  │         Integrations             │  │
│  │ (Tools)      │  │ (Execution)  │  │ (External Systems)               │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌─────────────────────┐ ┌─────────────┐ ┌─────────────────────┐
│    SDK Integrations   │ │ API Generators│ │   Protocol Bindings   │
│  ┌─────────┐          │ │              │ │  ┌─────────┐          │
│  │ Stripe   │          │ │ ┌─────────┐ │ │  │ MCP     │          │
│  │ AWS      │          │ │ │ OpenAPI │ │ │  │ JSON-RPC│          │
│  │ GCP      │          │ │ └─────────┘ │ │  │ gRPC    │          │
│  │ Azure    │          │ │ ┌─────────┐ │ │  │ WebSocket│         │
│  │ ...      │          │ │ │ Protobuf│ │ │  │ SSE     │          │
│  └─────────┘          │ │ └─────────┘ │ │  │ REST    │          │
└─────────────────────┘ └─────────────┘ └─────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌─────────────────────┐ ┌─────────────┐ ┌─────────────────────┐
│   Platform DevKits    │ │ Codegen     │ │    Domain Integrations│
│  ┌─────────┐          │ │ Systems     │ │  ┌─────────┐          │
│  │ Vercel   │          │ │              │ │  │ 3D       │          │
│  │ Supabase │          │ │ ┌─────────┐ │ │  │ Billing  │          │
│  │ Firebase │          │ │ │ Manifest│ │ │  │ Database│          │
│  │ ...      │          │ │ └─────────┘ │ │  │ Agents   │          │
│  └─────────┘          │ │              │ │  │ ...      │          │
└─────────────────────┘ └─────────────┘ └─────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        External Systems                                │
│  Stripe, AWS, GCP, Azure, Vercel, Supabase, Firebase, Blender, Three.js,  │
│  PostgreSQL, MongoDB, SQLite, Redis, LangChain, LlamaIndex, CrewAI, ...   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Appendix B: File Structure

```
skeletonkey/
├── __init__.py
├── cli.py                    # CLI entry point
├── toolkit.py                # Toolkit assembly
├── mcp/                      # MCP server and client
│   ├── __init__.py
│   ├── __main__.py           # MCP server entry point
│   ├── adapter.py            # MCP server adapter
│   └── client.py             # MCP client connector
├── core/                     # Core modules
│   ├── __init__.py
│   ├── config.py             # Configuration (ENHANCE for P5)
│   ├── engine.py             # Execution engine
│   ├── registry.py           # Tool registry (ENHANCE for P5)
│   ├── manifest.py           # Tool manifests
│   ├── profile.py            # Capability profile
│   ├── errors.py             # Error definitions
│   ├── envelope.py           # Result envelopes
│   ├── util.py               # Utilities
│   ├── validate.py           # Validation
│   ├── redact.py             # Redaction
│   ├── ledger.py             # Call ledger
│   ├── journal.py            # Change journal
│   ├── publish.py            # Publish system
│   └── semantic.py           # Semantic routing
├── fsx/                      # Filesystem
│   ├── __init__.py
│   ├── ops.py                # Filesystem operations
│   ├── sandbox.py            # Sandbox (ENHANCE for P5)
│   ├── journal.py            # Filesystem journal
│   └── search.py             # Search backend
├── shells/                   # Shell support
│   ├── __init__.py
│   ├── base.py               # Shell runner
│   ├── dialect.py            # Dialect support
│   └── execute.py            # Script execution
├── skills/                   # Skills system
│   ├── __init__.py
│   ├── loader.py             # Skill loader
│   ├── compiler.py           # Skill compiler
│   ├── install.py            # Skill installation
│   └── watch.py              # Skill watcher
├── tools/                    # Built-in tools
│   ├── __init__.py
│   └── builtin.py            # Built-in tool definitions
├── live/                     # Live HMR system
│   ├── __init__.py
│   ├── runtime.py            # Live runtime
│   ├── panel.py              # Live panel (ENHANCE for P5)
│   ├── patcher.py            # Patcher
│   ├── scene.py              # Scene graph
│   ├── watcher.py            # File watcher
│   ├── tools.py              # Live tools
│   └── demos.py              # Demo programs
├── protocols/                # NEW: Protocol bindings (P7)
│   ├── __init__.py
│   ├── base.py               # Base protocol classes
│   ├── jsonrpc.py            # JSON-RPC 2.0
│   ├── grpc.py               # gRPC
│   ├── websocket.py          # WebSocket
│   ├── sse.py                # Server-Sent Events
│   ├── rest.py               # REST
│   └── bridge.py             # Protocol bridge
├── integrations/             # NEW: Integrations (P5-P10)
│   ├── __init__.py
│   ├── registry.py           # Integration registry
│   ├── discovery.py          # Integration discovery
│   ├── health.py             # Health checks
│   ├── config.py             # Integration config
│   ├── errors.py             # Integration errors
│   ├── rest/                 # REST integrations (P6)
│   │   └── __init__.py
│   ├── graphql/              # GraphQL integrations (P6)
│   │   └── __init__.py
│   ├── grpc/                 # gRPC integrations (P6)
│   │   └── __init__.py
│   ├── websocket/            # WebSocket integrations (P6)
│   │   └── __init__.py
│   ├── sdk/                  # SDK integrations (P6)
│   │   └── __init__.py
│   ├── generators/           # Codegen systems (P8)
│   │   ├── __init__.py
│   │   ├── openapi/
│   │   ├── protobuf/
│   │   ├── json_schema/
│   │   └── manifest/
│   └── platforms/            # Platform DevKits (P9)
│       ├── __init__.py
│       ├── aws/
│       ├── gcp/
│       ├── azure/
│       ├── vercel/
│       ├── supabase/
│       └── firebase/
├── domains/                  # NEW: Domain integrations (P10)
│   ├── __init__.py
│   ├── threed/
│   │   ├── __init__.py
│   │   ├── blender.py
│   │   ├── threejs.py
│   │   └── cad.py
│   ├── billing/
│   │   ├── __init__.py
│   │   ├── stripe.py
│   │   ├── paddle.py
│   │   └── lemon_squeezy.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── postgres.py
│   │   ├── mongodb.py
│   │   ├── sqlite.py
│   │   └── redis.py
│   └── agents/
│       ├── __init__.py
│       ├── langchain.py
│       ├── llama_index.py
│       ├── crewai.py
│       ├── autogen.py
│       └── semantic_kernel.py
├── codegen/                 # NEW: Codegen systems (P8)
│   ├── __init__.py
│   ├── base.py
│   ├── templates/
│   ├── openapi/
│   ├── protobuf/
│   ├── json_schema/
│   └── manifest/
└── testing/                 # NEW: Testing framework (P11)
    ├── __init__.py
    └── integrations.py

config/
├── skeletonkey.example.toml  # Example config (UPDATE for P5)
└── integration.schema.json   # NEW: Integration config schema

docs/
├── TOOL-CONTRACT.md          # Tool contract
├── LIVE-HMR.md               # Live HMR documentation
├── LIVE-IMPL-PLAN.md         # Live implementation plan
├── SHELL-DIALECTS.md          # Shell dialects
├── SKILLS-SPEC.md            # Skills specification
├── SECURITY-MODEL.md         # Security model
├── CONNECT-A-HOST.md          # Host connection
├── adr/                      # Architecture Decision Records
│   ├── 0001-zero-dependency-core.md
│   ├── 0002-render-dont-rewrite.md
│   ├── ...
│   └── 0013-remote-tools-passthrough.md
└── integrations/              # NEW: Integration documentation
    ├── DEVELOPMENT.md
    ├── STRIPE.md
    ├── AWS.md
    └── ...

tests/
├── test_core_contracts.py
├── test_discovery.py
├── ...
└── integrations/              # NEW: Integration tests
    ├── __init__.py
    ├── test_rest.py
    ├── test_graphql.py
    ├── test_grpc.py
    ├── test_websocket.py
    └── ...

schemas/
├── skill-frontmatter.schema.json
├── tool-manifest.schema.json
├── tool-result.schema.json
└── integration-manifest.schema.json  # NEW: Integration schema

integrations/                 # NEW: Integration packs directory
├── stripe/
│   ├── INTEGRATION.md
│   ├── integration.toml
│   ├── tools.py
│   └── tests/
├── aws/
│   ├── INTEGRATION.md
│   ├── integration.toml
│   └── tools.py
└── ...
```

---

## Appendix C: Configuration Reference

### Integration Configuration

```toml
# Global integration settings
[integrations]
auto_discover = true
discover_paths = ["~/.skeletonkey/integrations", "/usr/local/share/skeletonkey/integrations"]
health_check_interval_s = 60

# Individual integration configuration
[integrations.stripe]
enabled = true
api_key = "{{PUB.stripe_api_key}}"
webhook_secret = "{{PUB.stripe_webhook_secret}}"
max_retries = 3
retry_delay_s = 1.0
rate_limit = 100  # requests per minute

[integrations.stripe.features]
payments = true
subscriptions = true
customers = true
invoices = true
webhooks = true

[integrations.aws]
enabled = true
region = "us-east-1"
credentials = { access_key_id = "{{PUB.aws_access_key_id}}", secret_access_key = "{{PUB.aws_secret_access_key}}" }

[integrations.aws.services]
ec2 = true
s3 = true
lambda = true
dynamodb = true
sqs = true
sns = true
cloudwatch = true

[integrations.postgres]
enabled = true
connection_string = "{{PUB.db_url}}"
pool_size = 10
pool_timeout_s = 30
statement_timeout_s = 60

[integrations.langchain]
enabled = true
api_key = "{{PUB.langchain_api_key}}"
project_id = "my-project"
base_url = "https://api.langchain.com"

[integrations.threed.blender]
enabled = true
executable = "/usr/bin/blender"
temp_dir = "/tmp/blender"
max_render_time_s = 3600
```

---

## Appendix D: Tool Manifest Examples

### REST API Tool Manifest

```json
{
  "id": "github.get_repo",
  "version": "1.0.0",
  "name": "GitHub Get Repository",
  "description": "Get information about a GitHub repository",
  "category": "api",
  "subcategory": "github",
  "capability": "github.api",
  "risk": "read",
  "group": "github",
  "provider": "rest",
  "tier": "full",
  "input_schema": {
    "type": "object",
    "properties": {
      "owner": {
        "type": "string",
        "description": "Repository owner (user or organization)",
        "minLength": 1
      },
      "repo": {
        "type": "string",
        "description": "Repository name",
        "minLength": 1
      }
    },
    "required": ["owner", "repo"],
    "additionalProperties": false
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "id": {"type": "integer"},
      "name": {"type": "string"},
      "full_name": {"type": "string"},
      "private": {"type": "boolean"},
      "html_url": {"type": "string", "format": "uri"}
    }
  },
  "requirements": [
    {"type": "binary", "name": "curl"},
    {"type": "env", "name": "GITHUB_TOKEN"}
  ],
  "tags": ["github", "api", "rest", "read"],
  "see_also": ["github.list_repos", "github.search_repos"],
  "anti_patterns": ["Don't use for private repos without proper auth"],
  "examples": [
    {
      "args": {"owner": "octocat", "repo": "Hello-World"},
      "note": "Get info about the Hello-World repo"
    }
  ],
  "source": "integration:github",
  "source_path": "integrations/github/tools.py",
  "advertised": true
}
```

### SDK Tool Manifest

```json
{
  "id": "stripe.create_customer",
  "version": "2024.10.0",
  "name": "Stripe Create Customer",
  "description": "Create a new customer in Stripe",
  "category": "billing",
  "subcategory": "stripe",
  "capability": "stripe.customers.write",
  "risk": "write",
  "group": "stripe",
  "provider": "sdk",
  "tier": "full",
  "input_schema": {
    "type": "object",
    "properties": {
      "email": {
        "type": "string",
        "format": "email",
        "description": "Customer email address",
        "minLength": 1
      },
      "name": {
        "type": "string",
        "description": "Customer full name",
        "minLength": 1
      },
      "description": {
        "type": "string",
        "description": "Customer description"
      },
      "metadata": {
        "type": "object",
        "description": "Set of key-value pairs for custom data",
        "additionalProperties": {"type": "string"}
      }
    },
    "required": ["email"],
    "additionalProperties": false
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "id": {"type": "string", "pattern": "^cus_"},
      "object": {"type": "string", "enum": ["customer"]},
      "email": {"type": "string", "format": "email"},
      "name": {"type": "string"},
      "created": {"type": "integer"}
    }
  },
  "requirements": [
    {"type": "python", "name": "stripe", "version": ">=5.0.0"}
  ],
  "tags": ["stripe", "billing", "customer", "write"],
  "see_also": ["stripe.get_customer", "stripe.update_customer", "stripe.delete_customer"],
  "anti_patterns": ["Don't store raw customer data in your database"],
  "examples": [
    {
      "args": {"email": "user@example.com", "name": "John Doe"},
      "note": "Create a customer with email and name"
    }
  ],
  "source": "integration:stripe",
  "source_path": "integrations/stripe/tools.py",
  "advertised": true,
  "reversible": false,
  "stateful": "host",
  "idempotent": false
}
```

---

## Appendix E: Error Codes for Integrations

| Code | Class | Message | Details | Retryable |
|------|-------|---------|---------|-----------|
| `INTEGRATION_DISABLED` | policy | Integration is disabled | `integration`, `reason` | false |
| `INTEGRATION_NOT_CONFIGURED` | policy | Integration is not configured | `integration`, `missing_keys` | false |
| `INTEGRATION_AUTH_FAILED` | execution | Authentication failed | `integration`, `auth_type`, `reason` | true |
| `INTEGRATION_RATE_LIMITED` | execution | Rate limit exceeded | `integration`, `limit`, `retry_after_s` | true |
| `INTEGRATION_TIMEOUT` | execution | Operation timed out | `integration`, `operation`, `timeout_s` | true |
| `INTEGRATION_UNAVAILABLE` | environment | Integration service unavailable | `integration`, `service`, `error` | true |
| `INTEGRATION_ERROR` | execution | Integration returned an error | `integration`, `operation`, `error_code`, `error_message` | false |
| `INTEGRATION_NOT_FOUND` | usage | Integration not found | `integration`, `available` | false |
| `INTEGRATION_VERSION_MISMATCH` | environment | Integration version incompatible | `integration`, `required`, `found` | false |
| `INTEGRATION_DEPENDENCY_MISSING` | environment | Required dependency missing | `integration`, `dependency`, `required_version` | false |

---

*Document maintained by Arena AI. Last updated: 2026-09-07.*
