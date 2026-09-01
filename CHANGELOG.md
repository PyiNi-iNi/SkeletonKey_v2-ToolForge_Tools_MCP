# Changelog

Semver on `skeletonkey/version.py` (`__version__`) and
`pyproject.toml [project].version` — both move together, one commit each
release. `sk --version` and `sk doctor` report the same value.

## 0.1.0 — first release

The installable, debuggable baseline (P1–P6):

- **Core** (zero mandatory dependencies, ADR-0001): manifest registry with
  tiers (`core`/`task`/`full`), capability probe + adaptive advertisement with
  a content digest, schema-subset argument validation, budget/spill envelope,
  journal-and-undo filesystem (`fs.*`), three shell dialects
  (bash/PowerShell/python) over one sentinel protocol, policy-as-data with
  grant receipts.
- **Skills**: `SKILL.md` packs discovered from `skills.dirs`, machine-checked
  frontmatter subset, declarative `tool.toml` tools compiled to real manifests
  (subprocess-only handlers, inlined scripts), load errors are reported not
  swallowed, hot reload for live programs.
- **MCP**: stdio server (`skeletonkey-mcp` console script), tools/list +
  tools/call + resources, remote MCP tool servers (`mcp.remotes`), approval
  flow, streamable HTTP transport.
- **Publish**: write-only publishing store with placeholder-based substitution
  (ADR-0010).
- **Live HMR**: file-watcher reload loop for agent programs, state
  export/import across patches (ADR-0011).
- **Semantic backend**: zero-dep lexical-tfidf tool routing, pluggable via
  entry points (ADR-0012).
- **Diagnostics**: `sk doctor` — one stable `schema: 1` JSON blob (config
  layers, probe receipts, gate diffs, ledger verification, spill dir, skill
  load errors); `sk doctor --fix` performs only the safe moves (create state
  dirs, force-probe).
- **Security**: pip-audit-clean extras tree; executable bypass matrix
  (`tests/test_security_matrix.py`, 11 tests) covering `..`, absolute-external,
  symlink escape, device names, `\\?\`, env injection, CLIXML spoofing,
  spill-path traversal; sentinel + path property tests.
- **Docs**: in-repo docs site (`docs/README.md` index): contract docs,
  write-a-skill tutorial, connect-a-host tutorial; all claims executed by the
  doc lint.

Migration notes: none — this is the first release; there is no prior
`SKELETONKEY_CONFIG`, state dir, or ledger format to migrate.
