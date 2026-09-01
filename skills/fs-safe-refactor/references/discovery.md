# Choosing the tool for the change

P5a discovery, in one screen. Ask the registry before you guess at a tool:

```console
registry.route {task: "rename a symbol in one file", k: 5}
```

The route is two-stage: an exact tool-id match always wins first, then the
deterministic lexical ranking. Every candidate carries `reasons` — which field
(name, capability, tags, description) matched which token — plus its `tier` and
`provider`. `fs.patch` wins "rename a symbol in one file"; `fs.search` and
`fs.read` will be in the shortlist too, because the discipline is
locate → read → patch → verify.

**A tool you expected is missing from `tools/list`.** `capabilities.explain
{fs.patch}` lists every tool claiming that capability (or the capability of a
given tool id) with each one's gate reasons, score, tier, live stats, and which
provider won and why. Two honest answers the explain tool will give:

- **Gated** — the tool is registered but this host failed a gate (platform,
  requirement, capability, `advertised = false`, or an `allow_*` knob). The
  reason names the knob or the unmet requirement.
- **Dropped by tier** — the tool is `task`/`full` and the advertised tier is
  smaller. `registry.expand {tier}` switches the surface (`core` → `task` →
  `full`); the default is `full`, so a host that never calls expand sees every
  tool it saw before. `registry.list` reports `tier`/`active_tier` plus the
  receipts.

Tiers govern *advertisement*, never authorization: a tool hidden by tier still
works if you call it by id. Only a gate can refuse a call.
