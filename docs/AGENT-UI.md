# Purpose-driven agent workspace

`sk agents` is the operator UI for choosing an autonomous agent by its job,
not by memorising tool IDs. It is built from the same live registry projection
as `sk viz`.

```console
$ sk agents -o agents.html       # offline standalone workspace
$ sk agents --json                # machine-readable purpose/tool map
$ sk agents --serve               # catalog at /, workspace at /agents
$ sk dev                          # mounted catalog at /tools and /tools/agents
```

## What the page shows

- **Purpose agents** — a mission, purpose statement, tier, call/time/output
  limits, risk note, and the actions that role is intended to perform.
- **Tooling contract** — every selected tool ID, capability, risk, and one of
  `ready`, `approval`, `agent read-only`, `tier gated`, or `host withheld`.
- **Coverage** — unique assigned tools versus all live registered tools. An
  unassigned tool is visible as a coverage failure; the builder never invents
  a generic role to hide it.
- **Policy boundary** — write actions are labelled policy-gated, destructive
  tools are not presented as safe, and the UI has no execution form or write
  route.

Use `/` to focus the filter. The page has inline CSS, inline JavaScript, and
an embedded JSON document, so it works from `file://` without npm, a CDN, or a
network connection.

## Runtime delegation

The page is intentionally a read-only projection. An application that wants to
run a role creates the existing restricted subagent boundary:

```python
from skeletonkey.agents import create_agent
from skeletonkey.core.subagent import call_as_subagent

agent = create_agent(toolkit.engine, "workspace-analyst")
result = call_as_subagent(agent, "fs.search", {
    "pattern": "TODO",
    "path": ".",
})
summary = agent.close()
```

`create_agent()` derives an allowlist from the current registry. Calls still
pass through host advertisement, tier/read-only checks, approvals, filesystem
roots, budgets, journaling, redaction, and the parent ledger. Profiles do not
grant capabilities that the engine does not already expose.

## HTTP routes

The standalone visualizer adds:

| route | method | purpose |
| --- | --- | --- |
| `/agents` | GET | purpose-driven workspace HTML |
| `/api/agents` | GET | same workspace model as JSON |

The mounted dev viewport prefixes these routes with `/tools`. The routes are
GET-only projections; they do not accept a task or call a tool. See
[ADR-0019](adr/0019-purpose-driven-agent-ui.md) for the contract and
[ADR-0018](adr/0018-mount-the-catalog-in-the-panel.md) for the mounted
read-only guard.
