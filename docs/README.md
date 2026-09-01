# SkeletonKey & ToolForge v2 — docs site

The reference for the whole project, in one place. Everything here is plain
Markdown in the repo (no hosted infrastructure, no build step), and
[`tests/test_docs.py`](../tests/test_docs.py) executes the claims: every tool
call shape, tool name, config key and error code this site names must exist in
the codebase.

## Start here

- **What it is** — [README](../README.md): two ways to drive it (CLI and
  Python), install, `sk doctor`.
- **Plan** — [PLAN.md](../PLAN.md): the phases, what is shipped, the exits;
  P6 is "others can install, run and debug this without reading the source."

## The contracts (spec-first: the code is written from these)

| Doc | What it pins down |
| --- | --- |
| [TOOL-CONTRACT](TOOL-CONTRACT.md) | tool result/error envelope, schema subset, `input_schema`, gates, adaptive advertisement, `REMOTE` tools |
| [SHELL-DIALECTS](SHELL-DIALECTS.md) | one protocol, three dialects (bash/PowerShell/python), argv-vs-shell, sentinel, strict mode |
| [SKILLS-SPEC](SKILLS-SPEC.md) | skill pack layout, frontmatter subset, `tool.toml`, discovery, hot reload |
| [SECURITY-MODEL](SECURITY-MODEL.md) | the sandbox, policy-as-data, abort/undo preconditions, redaction |
| [SECURITY-MATRIX](security-matrix.md) | *executed* red-team pass: every bypass attempt, its verdict, its receipt |

## Tutorials

- [Write a skill](site/write-a-skill.md) — turn procedural knowledge into a
  `SKILL.md` pack and (optionally) a declarative `tool.toml` tool.
- [Connect a host](site/connect-host.md) — Claude Desktop, any stdio MCP
  client, or the in-repo autopilot; plus remote tool servers.

## Decisions

[ADR index](adr/) — 14 records, including 0001 (zero-dependency core, the
promise `tests/test_core_imports.py` enforces), 0003 (sentinel protocol),
0007 (argv over interpolation), 0010 (publish store), 0011 (live HMR),
0012 (semantic backend), 0013 (remote passthrough), 0014 (discovery tiers).

## Build and test

```bash
pip install -e .[dev]
pytest            # doc lint included: this site cannot drift from the code
ruff check .
sk doctor         # one JSON blob; see README for the schema
```

## The site contract

- `docs/README.md` links only to files that exist (checked).
- The four contract docs are the boundary: a behavior change ships with its
  doc change in the same commit.
- New pages go under `docs/site/` and are added to `tests/test_docs.py`
  `DOC_FILES` (the lint is why you never have to re-read the source).
