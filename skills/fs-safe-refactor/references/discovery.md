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

**Everything withheld, in one call.** `registry.explain_all {}` lists every gated
tool with its reason (gate failure, tier withhold, tier-budget drop, or the tool's
own opt-out), gated rows first. `registry.explain_all {tier: "core"}` previews what
a smaller tier would hide without switching to it; `include_advertised: true` appends
the shown rows. Use it when more than one tool is missing — or when you want the
receipt before you call `registry.expand`.

**Remote tools** — a configured `[mcp.remotes.<server>]` shows up as
`remote.<server>.<tool>` with `source: "remote:<server>"` and honest risk
(unannotated ⇒ `write`, so approval gates it). The remote's error code passes
through untouched; `registry.stats {source: "remote:<server>"}` isolates its
rows. A remote server that failed to start is never advertised, and the reason
is in `registry.stats` → `overview.load_errors`.

Tiers govern *advertisement*, never authorization: a tool hidden by tier still
works if you call it by id. Only a gate can refuse a call.
