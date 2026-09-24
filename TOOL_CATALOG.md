# SkeletonKey / ToolForge v2 — Comprehensive Tool & Feature Catalog

**Generated**: 2026-09-19  
**Total Source Lines**: 27,125 (core modules only)  
**Registered Engine Tools**: 81  
**Loaded Integration Tools**: 43 (simple-demo: 3, sqlite: 10, github: 18, stripe: 12)  
**Discovered Projects**: 9 (8 dropins + 1 skill)  

---

## 1. Engine Tools (81 Total)

### 1.1 Core Registry Tools (8)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `registry.list` | List Tools | none | core | registry | builtin |
| `registry.describe` | Describe Tool | none | core | registry | builtin |
| `registry.search` | Search Tools | none | core | registry | builtin |
| `registry.route` | Route Task to Tools | none | core | registry | builtin |
| `registry.expand` | Expand Tier | none | core | registry | builtin |
| `registry.stats` | Registry Statistics | none | core | registry | builtin |
| `registry.explain_all` | Explain All Tools | none | core | registry | builtin |
| `capabilities.explain` | Explain Capabilities | none | core | capabilities | builtin |

### 1.2 Filesystem Tools (16)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `fs.read` | Read File | read | task | fs | builtin |
| `fs.write` | Write File | write | task | fs | builtin |
| `fs.patch` | Patch File | write | task | fs | builtin |
| `fs.list` | List Directory | read | task | fs | builtin |
| `fs.stat` | Stat Path | read | task | fs | builtin |
| `fs.glob` | Glob Pattern | read | task | fs | builtin |
| `fs.search` | Search Files | read | task | fs | builtin |
| `fs.sniff` | Sniff File Type | read | task | fs | builtin |
| `fs.mkdir` | Create Directory | write | task | fs | builtin |
| `fs.move` | Move/Rename | write | task | fs | builtin |
| `fs.chmod` | Change Permissions | write | task | fs | builtin |
| `fs.delete` | Delete Path | destructive | task | fs | builtin |
| `fs.undo` | Undo Last Op | write | task | fs | builtin |
| `fs.undo_task` | Undo Task | write | task | fs | builtin |
| `fs.journal_list` | List Journal | read | task | fs | builtin |
| `fs.redo` | Redo Last Undo | write | task | fs | builtin |

**File**: `skeletonkey/fsx/ops.py` (1,205 lines)  
**Sandbox**: `skeletonkey/fsx/sandbox.py` (693 lines)  
**Journal**: `skeletonkey/fsx/journal.py` (874 lines)  
**Search**: `skeletonkey/fsx/search.py` (372 lines)

### 1.3 Shell Tools (10)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `shell.run` | Run Script | write | task | shell | builtin |
| `shell.jobs` | List Jobs | none | task | shell | builtin |
| `shell.job_wait` | Wait for Job | none | task | shell | builtin |
| `shell.job_watch` | Watch Job | none | task | shell | builtin |
| `shell.job_kill` | Kill Job | destructive | task | shell | builtin |
| `shell.sessions` | List Sessions | none | task | shell | builtin |
| `shell.session_reset` | Reset Session | write | task | shell | builtin |
| `shell.quote` | Quote Args | none | task | shell | builtin |
| `shell.quote_check` | Check Quote | none | full | shell | builtin |
| `shell.selftest` | Shell Selftest | read | full | shell | builtin |

**Files**: `skeletonkey/shells/base.py` (815), `execute.py` (154), `dialect.py` (543)

### 1.4 Skills Tools (4)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `skills.list` | List Skills | none | core | skills | builtin |
| `skills.load` | Load Skill | none | core | skills | builtin |
| `skills.match` | Match Skills | none | core | skills | builtin |
| `skills.install` | Install Skill | write | task | skills | builtin |
| `skills.uninstall` | Uninstall Skill | destructive | task | skills | builtin |

**Loader**: `skeletonkey/skills/loader.py`, `compiler.py`, `install.py`, `watch.py`

### 1.5 Profile & Policy Tools (3)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `profile.probe` | Probe Capabilities | none | core | profile | builtin |
| `policy.grant` | Grant Capability | write | task | policy | builtin |

**Config**: `skeletonkey/core/config.py` (537), `policy.py` (345)

### 1.6 Publishing Tools (8)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `pub.store_put` | Store Secret | write | full | publishing | builtin |
| `pub.store_list` | List Secrets | read | full | publishing | builtin |
| `pub.store_delete` | Delete Secret | write | full | publishing | builtin |
| `pub.placeholders` | Find Placeholders | read | full | publishing | builtin |
| `pub.inject` | Inject Secrets | write | full | publishing | builtin |
| `pub.platforms` | List Platforms | read | full | publishing | builtin |
| `pub.payments` | Payment Providers | read | full | publishing | builtin |
| `pub.packaging` | Packaging Targets | read | full | publishing | builtin |
| `pub.testers` | Tester Platforms | read | full | publishing | builtin |

**File**: `skeletonkey/core/publish.py`

### 1.7 Live/HMR Tools (10)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `live.start` | Start HMR | write | full | live | live |
| `live.stop` | Stop HMR | write | full | live | live |
| `live.status` | HMR Status | read | full | live | live |
| `live.reload` | Reload Module | write | full | live | live |
| `live.patch` | Patch Code | write | full | live | live |
| `live.repl` | REPL Exec | write | full | live | live |
| `live.state` | Inspect State | read | full | live | live |
| `live.snapshot` | Snapshot | write | full | live | live |
| `live.render` | Render Frame | read | full | live | live |
| `live.scene` | Scene Graph | write | full | live | live |
| `live.serve` | Preview Server | write | full | live | live |

**Files**: `skeletonkey/live/runtime.py` (1,063), `panel.py` (941), `tools.py` (472), `watcher.py` (218)

### 1.8 Planner Tools (2)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `planner.plan` | Create Plan | none | full | planner | planner |
| `planner.export` | Export Plan | none | full | planner | planner |

**File**: `skeletonkey/core/planner.py` (378)

### 1.9 Remote/Quarantine/Subagent Tools (3)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `remote.probe` | Probe Remote | read | full | remote | remote |
| `quarantine.exec` | Exec in Quarantine | write | full | quarantine | quarantine |
| `subagent.spawn` | Spawn Subagent | write | full | subagent | subagent |

**Files**: `skeletonkey/remote/targets.py` (459), `core/quarantine.py` (458), `core/subagent.py` (382)

### 1.10 Export Tools (1)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `export.manifest` | Export Manifest | none | full | export | export |

**File**: `skeletonkey/core/export.py` (256)

### 1.11 Integration Management Tools (17)

| Tool ID | Title | Risk | Tier | Group | Source |
|---------|-------|------|------|-------|--------|
| `integrations.list` | List Integrations | none | task | integrations | integration_tools |
| `integrations.describe` | Describe Integration | none | task | integrations | integration_tools |
| `integrations.load` | Load Integration | write | task | integrations | integration_tools |
| `integrations.unload` | Unload Integration | write | task | integrations | integration_tools |
| `integrations.enable` | Enable Integration | write | task | integrations | integration_tools |
| `integrations.disable` | Disable Integration | write | task | integrations | integration_tools |
| `integrations.health` | Check Health | none | task | integrations | integration_tools |
| `integrations.stats` | Statistics | none | task | integrations | integration_tools |
| `integrations.config` | Get/Set Config | write | task | integrations | integration_tools |
| `integrations.secrets` | Manage Secrets | write | task | integrations | integration_tools |
| `integrations.discover` | Discover Integrations | none | task | integrations | integration_tools |
| `integrations.validate` | Validate Integrations | none | task | integrations | integration_tools |
| `integrations.packs.list` | List Packs | none | task | integrations | integration_tools |
| `integrations.packs.install` | Install Pack | write | task | integrations | integration_tools |
| `integrations.packs.uninstall` | Uninstall Pack | destructive | task | integrations | integration_tools |
| `integrations.packs.info` | Pack Info | none | task | integrations | integration_tools |
| `integrations.packs.updates` | Check Updates | none | task | integrations | integration_tools |

**File**: `skeletonkey/core/integration_tools.py` (825 lines)  
**Manager**: `skeletonkey/integrations/__init__.py` (1,795 lines)

---

## 2. Integration Tools (43 when loaded)

### 2.1 simple-demo (3 tools)
| Tool ID | Title | Risk | Tier |
|---------|-------|------|------|
| `simple-demo.echo` | Echo | read | task |
| `simple-demo.greet` | Greet | read | task |
| `simple-demo.add` | Add Numbers | read | task |

**File**: `skeletonkey/integrations/examples/simple_demo.py` (169 lines)

### 2.2 sqlite (10 tools)
| Tool ID | Title | Risk | Tier |
|---------|-------|------|------|
| `sqlite.execute` | Execute SQL | write | task |
| `sqlite.query` | Query Database | read | task |
| `sqlite.create_table` | Create Table | write | task |
| `sqlite.insert` | Insert Data | write | task |
| `sqlite.select` | Select Data | read | task |
| `sqlite.update` | Update Data | write | task |
| `sqlite.delete` | Delete Data | write | task |
| `sqlite.transaction` | Execute Transaction | write | task |
| `sqlite.tables` | List Tables | read | task |
| `sqlite.schema` | Get Table Schema | read | task |

**File**: `skeletonkey/integrations/categories/databases/sqlite.py` (800 lines)

### 2.3 github (18 tools)
| Tool ID | Title | Risk | Tier |
|---------|-------|------|------|
| `github.rate_limit` | Rate Limit | read | task |
| `github.user.get` | Get User | read | task |
| `github.repo.get` | Get Repository | read | task |
| `github.repo.list` | List Repositories | read | task |
| `github.repo.contents` | Get Repository Contents | read | task |
| `github.repo.tags` | List Tags | read | task |
| `github.repo.branches` | List Branches | read | task |
| `github.releases.list` | List Releases | read | task |
| `github.issues.list` | List Issues | read | task |
| `github.issues.get` | Get Issue | read | task |
| `github.issues.create` | Create Issue | write | task |
| `github.issues.comment` | Comment on Issue | write | task |
| `github.pulls.list` | List Pull Requests | read | task |
| `github.pulls.get` | Get Pull Request | read | task |
| `github.pulls.create` | Create Pull Request | write | task |
| `github.search.repos` | Search Repositories | read | task |
| `github.search.code` | Search Code | read | task |
| `github.contents.update` | Create/Update File | write | task |

**File**: `skeletonkey/integrations/categories/devtools/github.py` (not loaded, requires PyGithub)

### 2.4 stripe (12 tools)
| Tool ID | Title | Risk | Tier |
|---------|-------|------|------|
| `stripe.balance` | Retrieve Balance | read | task |
| `stripe.charges.list` | List Charges | read | task |
| `stripe.charges.retrieve` | Retrieve Charge | read | task |
| `stripe.charges.create` | Create Charge | write | task |
| `stripe.customers.list` | List Customers | read | task |
| `stripe.customers.retrieve` | Retrieve Customer | read | task |
| `stripe.customers.create` | Create Customer | write | task |
| `stripe.payment_intents.create` | Create Payment Intent | write | task |
| `stripe.payment_intents.retrieve` | Retrieve Payment Intent | read | task |
| `stripe.invoices.list` | List Invoices | read | task |
| `stripe.products.list` | List Products | read | task |
| `stripe.prices.list` | List Prices | read | task |

---

## 3. Discovered Projects (9)

| Project Name | Type | Path | Tools | Enabled |
|-------------|------|------|-------|---------|
| `live_hmr` | dropin | examples/live_hmr | 1 (orbital) | yes |
| `features` | dropin | features | 1 (conftest) | yes |
| `skeletonkey` | dropin | skeletonkey | 6 (cli, diagnostics, publish_data, toolkit, version, wire) | yes |
| `fs-safe-refactor` | skill | skills/fs-safe-refactor | 0 | yes |
| `publishing` | skill | skills/publishing | 0 | yes |
| `python-env-bootstrap` | skill | skills/python-env-bootstrap | 0 | yes |
| `shell-crossplatform` | skill | skills/shell-crossplatform | 0 | yes |
| `vcs-git-safely` | skill | skills/vcs-git-safely | 0 | yes |
| `tests` | dropin | tests | 41 (test modules) | yes |

**Project Indexer**: `skeletonkey/core/projects.py` (1,146 lines)  
**Watch Mode**: Polling + watchfiles support, CLI `sk projects watch`

---

## 4. Available but Not Loaded Integrations (require deps)

| Integration | Type | Category | Required Deps |
|-------------|------|----------|---------------|
| `postgresql` | database | database | psycopg2 |
| `mongodb` | database | database | pymongo |
| `redis` | database | database | redis |
| `aws-s3` | cloud | cloud | boto3 |
| `requests` | http-client | http_clients | requests |

**Category Framework**: `skeletonkey/integrations/categories/` (databases, cloud, http_clients, devtools)

---

## 5. Core Architecture Files

| File | Lines | Purpose |
|------|-------|---------|
| `skeletonkey/__init__.py` | 36 | Package exports |
| `skeletonkey/toolkit.py` | 684 | Assembly layer - builds complete toolkit |
| `skeletonkey/cli.py` | 1,372 | CLI entry point (`sk` command) |
| `skeletonkey/core/config.py` | 537 | Layered configuration (TOML, env, overrides) |
| `skeletonkey/core/engine.py` | 920 | Execution engine, budget, approvals |
| `skeletonkey/core/registry.py` | 1,043 | Tool registry, routing, tiers |
| `skeletonkey/core/manifest.py` | 287 | ToolManifest dataclass |
| `skeletonkey/core/errors.py` | 228 | Error codes & SkeletonKeyError |
| `skeletonkey/core/policy.py` | 345 | Policy engine (deny/allow/escalate/rate_limit) |
| `skeletonkey/core/projects.py` | 1,146 | Project indexer & discovery |
| `skeletonkey/core/secrets.py` | 636 | AES-256-GCM secret store |
| `skeletonkey/core/integration_tools.py` | 825 | Integration management engine tools |
| `skeletonkey/core/export.py` | 256 | OpenAI/Anthropic/MCP export |
| `skeletonkey/core/planner.py` | 378 | DAG planner, parallel_safe analysis |
| `skeletonkey/core/quarantine.py` | 458 | Restricted subprocess execution |
| `skeletonkey/core/subagent.py` | 382 | Restricted engine boundary |
| `skeletonkey/core/ledger.py` | ~ | Audit logging |
| `skeletonkey/core/envelope.py` | ~ | Call envelope |
| `skeletonkey/core/replay.py` | ~ | Deterministic replay |
| `skeletonkey/core/redact.py` | ~ | Secret redaction |

---

## 6. Integration Framework Files

| File | Lines | Purpose |
|------|-------|---------|
| `skeletonkey/integrations/__init__.py` | 1,795 | Core framework: enums, base classes, registry, manager |
| `skeletonkey/integrations/rest.py` | 165 | RestIntegration base (stdlib urllib) |
| `skeletonkey/integrations/discovery.py` | 687 | Multi-source discovery (entrypoints, fs, packages, config) |
| `skeletonkey/integrations/loader.py` | 879 | Dynamic loading, topological dep resolution |
| `skeletonkey/integrations/packs.py` | 1,049 | Pack install/upgrade/uninstall (local/git/PyPI) |
| `skeletonkey/integrations/bridge.py` | 187 | ToolManifest wiring to engine registry |
| `skeletonkey/integrations/validation.py` | 475 | Static validation (metadata, tools, deps, secrets, compat) |
| `schemas/integration-manifest.schema.json` | 388 | JSON Schema for integration manifests |

---

## 7. Live/HMR System

| File | Lines | Purpose |
|------|-------|---------|
| `skeletonkey/live/runtime.py` | 1,063 | LiveManager, program lifecycle, patching |
| `skeletonkey/live/panel.py` | 941 | Preview panel HTTP server |
| `skeletonkey/live/tools.py` | 472 | Live engine tools |
| `skeletonkey/live/watcher.py` | 218 | File watching (watchfiles) |
| `skeletonkey/live/scene.py` | ~ | Scene graph for rendering |
| `skeletonkey/live/demos.py` | ~ | Demo programs |

---

## 8. Shell System

| File | Lines | Purpose |
|------|-------|---------|
| `skeletonkey/shells/base.py` | 815 | ShellRunner, session management |
| `skeletonkey/shells/execute.py` | 154 | Script execution |
| `skeletonkey/shells/dialect.py` | 543 | Dialect detection (bash/pwsh/python/sh/zsh) |

---

## 9. Remote/Quarantine/Subagent

| File | Lines | Purpose |
|------|-------|---------|
| `skeletonkey/remote/targets.py` | 459 | Local/Docker/SSH target probing |
| `skeletonkey/remote/__init__.py` | ~ | Remote connector |
| `skeletonkey/core/quarantine.py` | 458 | Subprocess isolation |
| `skeletonkey/core/subagent.py` | 382 | Restricted engine view |

---

## 10. Configuration Schema (skeletonkey.toml)

```toml
[roots]                    # Filesystem roots (security boundary)
cwd = ""                   # Working directory
workspace = ""             # Workspace root

[policy]                   # Risk gating
read_only = false
auto_approve = ["none", "read", "write"]
require_approval = ["destructive", "privileged", "network"]
deny = ["fs.delete(**/.ssh/**)", "fs.delete(**/cookies*)"]
max_mutations_per_minute = 120

[budget]                   # Governor limits
max_output_bytes = 24000
task_max_calls = 400
task_max_mutations = 150

[shell]                    # Shell config
default_dialect = null
allow_dialects = ["bash", "pwsh", "powershell", "python", "sh", "zsh"]
timeout_s = 120.0

[fs]                       # Filesystem config
follow_symlinks = "within-roots"
deny = ["**/.env", "**/id_rsa", "**/.ssh/**"]

[skills]                   # Skills config
dirs = ["skills"]
auto_load = true

[tools]                    # Tool config
dropin_dirs = ["tools"]
semantic = false

[advertise]                # MCP advertisement budgets
core_max_tools = 20
task_max_tools = 48

[mcp]                      # MCP server config
transport = "stdio"
port = 8765

[state]                    # State directory
dir = ""                   # Auto: <workspace>/.sk
journal = true
ledger = true

[integrations]             # P5 Integration config
enabled = []
disabled = []
auto_load = false
secrets_dir = ""
packs_dir = "integrations"
default_sandbox = true

[integrations.configs.<name>]  # Per-integration config

[live]                     # Live/HMR
enabled = true
host = "127.0.0.1"
port = 8010
```

**Env Prefix**: `SKELETONKEY_` (e.g., `SKELETONKEY_INTEGRATIONS_ENABLED=sqlite,github`)

---

## 11. Key Features Summary

### Security
- **Path Sandbox**: Single chokepoint for all filesystem access, symlink escape prevention
- **Per-Integration Sandbox**: Additive isolation (allowed/denied paths, network hosts)
- **Secret Store**: AES-256-GCM encryption, PBKDF2 key derivation, env fallback
- **Policy Engine**: Deny/allow/escalate/rate_limit rules with glob matching
- **Budget Governor**: Per-task call/mutation/token limits, circuit breaker

### Integration Framework
- **15 Integration Types**: SDK, API, client-library, api-generator, framework, toolchain, CLI, runtime, protocol-binding, codegen, platform-devkit, 3d-modeling, billing-payment, database, agent-harness
- **17 Categories**: cloud, database, ai-ml, web, mobile, desktop, serverless, devtools, monitoring, security, payment, 3d-graphics, collaboration, storage, networking, messaging, analytics
- **23 Platforms**: AWS, GCP, Azure, Vercel, Supabase, Firebase, Cloudflare, Linux, Windows, macOS, Node.js, Python, etc.
- **Lifecycle**: register → discover → load → enable → health → unload → disable
- **Dependency Resolution**: Topological sort (Kahn's algorithm), cycle detection
- **Pack Management**: Local dir, git repo, PyPI; version constraints; auto-dep install

### Discovery & Loading
- **5 Discovery Sources**: Registry (priority), entrypoints, filesystem, packages, config
- **Dynamic Loading**: From file, module, or path; hot-reload support
- **Validation**: Metadata, tool mapping, schemas, deps, security scan, compatibility

### Live Development (HMR)
- **Hot Module Reload**: Patch/replace functions/classes at runtime
- **Preview Panel**: HTTP server with REPL execution
- **Scene Graph**: Structured rendering with state snapshots
- **Watch Mode**: watchfiles-backed file watching

### Observability
- **Structured Ledger**: NDJSON audit log with redaction
- **Journal**: Filesystem operation history with undo
- **Replay**: Deterministic re-execution with diff
- **Export**: OpenAI Functions, Anthropic Tools, MCP Manifest

---

## 12. CLI Commands

```bash
sk profile [--force] [--receipts]
sk tools [list|search|route|expand|describe|stats] [--tier core|task|full] [--semantic]
sk shell <script> [--dialect bash|pwsh|python|sh|zsh] [--session] [--background]
sk jobs [list|wait|watch|kill] [job_id]
sk fs [ls|cat|write|patch|search|glob|stat|rm|mv|mkdir|chmod|undo|journal]
sk skills [list|load|match|install|uninstall] [--dry-run]
sk projects [list|describe|enable|disable|scan|stats|watch] [--type] [--interval 0.5]
sk integrations [list|describe|enable|disable|load|unload|health|stats] [--type] [--category] [--platform]
sk pub [list|put|delete|placeholders|inject|platforms|payments|packaging|testers]
sk live [start|stop|status|reload|patch|repl|state|snapshot|render|scene|serve|demo]
sk mcp [--transport stdio|streamable-http] [--host] [--port]
sk wire [hosts...] [--project] [--transport] [--port] [--remove] [--check]
sk doctor [--config] [--fix] [--no-probe]
sk replay <ref>
sk eval --suite <file>...
sk export [openai-tools|anthropic-tools|mcp-manifest|list] [--tier] [--output]
sk call <tool> '<json args>' [--as-subagent]
sk describe
sk --json <any>  # JSON output
sk --auto-approve  # Autopilot mode
```

---

## 13. Test Coverage

| Test File | Focus | Count |
|-----------|-------|-------|
| `tests/test_integration_loader.py` | Dynamic loading, deps, conflicts | 9 |
| `tests/test_integration_packs.py` | Pack install/upgrade/uninstall | 1 |
| `tests/test_integration_validation.py` | Static validation, stub APIs | 21 |
| `tests/test_core_contracts.py` | Engine contracts | ~ |
| `tests/test_registry_config.py` | Config layering | 38 |
| `tests/test_tools_builtin.py` | Builtin tools | ~ |
| `tests/test_sandbox.py` | Path sandbox | ~ |
| `tests/test_live.py` | HMR system | ~ |
| `tests/test_planner.py` | DAG planner | ~ |
| `tests/test_quarantine.py` | Subprocess isolation | ~ |
| `tests/test_subagent.py` | Subagent boundary | ~ |
| `tests/test_export.py` | Tool export | ~ |
| `tests/test_remote_targets.py` | Remote probing | ~ |
| `tests/test_skills.py` | Skills system | ~ |
| `tests/test_p7_engine_integration.py` | P7 features | ~ |

---

## 14. Schemas

| Schema File | Purpose |
|-------------|---------|
| `schemas/tool-manifest.schema.json` | ToolManifest validation |
| `schemas/skill-frontmatter.schema.json` | SKILL.md frontmatter |
| `schemas/tool-result.schema.json` | ToolResult envelope |
| `schemas/integration-manifest.schema.json` | Integration manifest |

---

## 15. Example Configurations

### Minimal skeletonkey.toml
```toml
[roots]
# Auto-detects workspace

[integrations]
enabled = ["sqlite", "github"]
auto_load = true
```

### Full Integration Config
```toml
[integrations]
enabled = ["sqlite", "github", "stripe", "aws-s3"]
auto_load = true
secrets_dir = ".sk/integrations/secrets"
packs_dir = "integrations"

[integrations.configs.github]
base_url = "https://api.github.com"

[integrations.configs.stripe]
api_version = "2023-10-16"

[integrations.configs.aws-s3]
region = "us-west-2"
```

---

**End of Catalog**  
*Generated from source analysis of 27,125 lines across 47 core modules*