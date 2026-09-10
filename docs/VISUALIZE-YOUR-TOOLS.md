# Visualise your tools

You have a list of tools somewhere — OpenAI function schemas, an MCP
`tools/list` response, a dict of handlers. This turns it into a single
self-contained HTML page you can open, search, and commit.

You do not need to adopt SkeletonKey to do this. Nothing here registers your
tools, calls them, or changes how your agent runs.

## 15 lines

```python
from skeletonkey.viz import build_viz_model, render_html

MY_TOOLS = [
    {"name": "db.query",   "description": "Run a read-only SQL query"},
    {"name": "db.migrate", "description": "Apply a migration", "risk": "write"},
    {"name": "email.send", "description": "Send an email", "risk": "write"},
]

model = build_viz_model(MY_TOOLS)
with open("tools.html", "w", encoding="utf-8") as fh:
    fh.write(render_html(model, title="My project"))
```

Open `tools.html`. One file, no server, no CDN, no network calls.

## From the command line

```bash
sk viz --from tools.json -o tools.html     # a page
sk viz --from tools.json --summary         # a text summary
```

`tools.json` can be any of the shapes below.

## What it accepts

| Shape | Example |
|---|---|
| OpenAI / Anthropic functions | `[{"type": "function", "function": {"name": ..., "parameters": ...}}]` |
| Bare function schemas | `[{"name": ..., "description": ..., "parameters": ...}]` |
| MCP `tools/list` | `{"tools": [{"name": ..., "inputSchema": ...}]}` |
| A mapping | `{"db.query": {"description": ...}}` |
| SkeletonKey manifests | `[ToolManifest(id=...), ...]` |
| A real `Registry` | passed straight through |

Only a name is required. Everything else is optional and used if present:
`description`, `risk`, `parameters`/`inputSchema`/`input_schema`/`schema`,
`tags`, `destructive`, `reversible`, `idempotent`, `title`.

Tools with dotted ids (`db.query`) are grouped by their prefix. Duplicate ids
resolve last-wins.

## What it will not do

**It does not guess whether your tools are dangerous.**

A tool called `db.delete_everything` that declares no risk is shown as
**"risk not declared"**, not as `destructive`. The `--summary` output prints
`undeclared` in the risk column for the same tools.

This is deliberate, and it is the one design decision worth arguing about. A
name-sniffing heuristic would be right often enough to be trusted and wrong
often enough to hurt: it would flag `cache.delete` as destructive and miss
`account.close`. A page that renders a confident red badge nobody actually
stood behind is worse than a page that admits it doesn't know, because the
first one ends a conversation and the second one starts one.

To get real risk classes, declare them — `"risk": "write"` in the source, or
adopt `ToolManifest` properly. Valid values: `none`, `read`, `write`,
`destructive`, `network`, `privileged`.

**Adapted tools cannot be called.** They are documentation. The adapter
installs a handler that raises `RuntimeError` if anything invokes it, rather
than a no-op that would let a caller believe something ran.

## Committing the summary

`sk viz --summary` produces a stable, diff-reviewable text file — one line per
tool. Commit it and a reviewer sees, in the PR diff, that a tool was added or
that a risk class changed:

```bash
sk viz --summary -o docs/TOOLSET.txt
```

See [the CI gate](#ci-gate) below to keep it honest.

## CI gate

`--check` compares the live toolset against the committed snapshot, prints a
diff, and exits 1 if they differ. It never rewrites the file.

```yaml
- name: Toolset snapshot is current
  run: sk viz --summary --check -o docs/TOOLSET.txt
```

Any project, no pytest dependency.

### What this gate does and does not prove

It catches: tools added or removed, a risk class changed, exposure flipped
from advertised to withheld, a token budget moving.

It does **not** catch a tool whose *behaviour* changed while its manifest
stayed identical. `db.query` can start writing to disk tomorrow and this diff
will be empty and green.

Please do not read a clean snapshot as a safety review. It proves the shape of
your toolset is what you last agreed to — which is genuinely useful, and is
strictly less than "these tools are safe".
