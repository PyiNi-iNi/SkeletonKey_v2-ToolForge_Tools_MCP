# The tool visualizer

`sk viz` renders the whole toolset — every registered tool, advertised or not —
as one document you can read, share, serve, diff, or fail a build on.

It is a **read-only projection** of `registry.all()` + `registry.advertise()`
and holds no facts of its own (ADR-0016). Stdlib only: no npm, no CDN, no build
step, and the written page opens from `file://` with the network off.

```console
$ sk viz -o tools.html          # a standalone page: mail it, commit it, diff it
$ sk viz --serve                # the same page, watching for drift
$ sk viz --json | jq .aggregates
$ sk viz --why-not              # every withheld tool, and what would fix it
$ sk viz --drift                # lint the manifests; non-zero exit if any drifted
```

## The six views

| view | question it answers |
| --- | --- |
| **cards** | what can this host do? (filter by group, risk, tier, exposure) |
| **graph** | how do the tools relate? (`see_also`, capability contests, anti-patterns) |
| **tokens** | what is my context window paying for? |
| **matrix** | how is the surface distributed across group × risk, tier × risk? |
| **latency** | how expensive are these tools, by declared cost? |
| **runtime** | what actually happened, and which manifests are now lying? |

All six are server-rendered markup. Script only toggles which is visible, so a
browser with JavaScript disabled still gets the complete catalog.

## Reading the graph

- **colour** — group
- **ring** — risk class
- **size** — token cost (area-proportional)
- **dashed outline** — registered but withheld on this host
- **`!` glyph** — destructive

Risk and destructiveness are encoded twice (colour *and* shape) so meaning
survives greyscale printing and colour blindness. Hover or keyboard-focus a node
to light its neighbourhood; click any node, tile or row to open the detail
drawer.

## Why-not: the decision chain

The most valuable question about a tool is why you cannot call it. Every tool
carries a 10-step chain mirroring `Registry.gate()`/`advertise()` in order:

| # | rule | fails when |
| --- | --- | --- |
| 1 | `tools.disable` | the id is disabled in config (short-circuits) |
| 2 | `manifest.advertised` | *records* an opt-out; the verdict lands at step 8 |
| 3 | `policy.read_only` | the host is read-only and the tool mutates |
| 4 | `manifest.platforms` | the tool's OS list excludes this host |
| 5 | `requirements` | a required binary/module/capability is missing |
| 6 | `require_any` | none of the alternatives are available |
| 7 | `discovery.tier` | the tool's tier is wider than the active one |
| 8 | `internal` | the manifest opted out of advertisement |
| 9 | `provider.dedupe` | another provider won this capability |
| 10 | `advertise.budget` | the token/`max_tools` cap trimmed it |

Each step reports `pass | fail | note | skip`, its evidence, and — on failure —
an actionable fix. The invariant that keeps this honest, asserted for every tool
under every projection: **a tool is advertised iff its chain has no failing
step.**

```console
$ sk viz --explain shell.run
$ sk viz --why-not --json | jq 'keys'
```

## Hypothetical hosts, and diffing

`--as-host` answers "what would a Windows box advertise?" by deriving a profile
from this host. It never re-probes and never mutates the live profile; the
result is stamped `fingerprint=hypothetical:<os>` so nothing downstream mistakes
it for a real probe.

```console
$ sk viz --as-host windows -o windows.json
$ sk viz --diff windows.json        # against this host
$ sk viz --diff main-branch.json    # or against another commit
```

`--diff` reports added, removed, field-level changes, exposure flips (with the
reason) and a token delta. It works on saved `--json` files, so two hosts or two
commits can be compared with no live registry.

## Runtime overlay and the drift linter

`--runtime` reads the ledger and puts measurements beside the declarations:
call counts, success rate, p50/p95/max latency, error codes, and a redacted
feed of recent calls.

`find_drift()` reports where a manifest has stopped telling the truth:

- **latency drift** — measured p95 exceeds declared `typical_latency_ms` by more
  than 3× (forgiving on purpose: cold caches and first imports cost real time);
- **reliability drift** — the tool fails more than half the time.

It refuses to accuse on fewer than 5 calls, because a percentile over two
samples is an anecdote.

```console
$ sk viz --drift        # exits 1 when a manifest has drifted — a CI gate
```

The overlay is **absent by default and absent-safe**: no ledger, an empty one, a
torn one, or one that raises all yield an empty overlay and an unchanged page. A
model without an overlay serialises byte-identically to one from before V5.

## The committed snapshot

The page is ~300 KB of HTML. That is a fine thing to look at and a useless
thing to review. So the repo also commits `docs/TOOLSET.txt`, a compact text
rendering of the same model - one sorted line per registered tool:

```
fs.delete                          task  destructive   164tok DR-- advertised
shell.selftest                     full  none           58tok --IP withheld:profile
```

The flag column is `D`estructive / `R`eversible / `I`dempotent /
`P`arallel-safe. Regenerate it whenever the toolset changes:

```bash
sk viz --summary -o docs/TOOLSET.txt
```

`tests/test_viz_ship.py` fails if that file is stale, which is the point: a new
destructive tool, a tool that silently stops being advertised, or a risk class
that changes now shows up as a one-line diff in review rather than as a
surprise in production.

## Surfaces

| surface | how |
| --- | --- |
| CLI | `sk viz [--json\|-o FILE] [--serve] [--explain\|--why-not] [--as-host] [--diff] [--runtime\|--drift] [--tier] [--read-only]` |
| export | `sk export viz-html -o tools.html` |
| HTTP | `sk viz --serve` → `/`, `/api/model`, `/api/digest`, `/api/explain`, `/api/runtime`, `/api/tool/<id>`, `/healthz` |
| MCP | resource `skeletonkey://viz/model` |
| Python | `from skeletonkey.viz import build_viz_model, render_html` |
| doctor | the `viz` check fails if the model stops covering every registered tool |

The served page and the written file come from the same renderer; serving only
adds a banner that polls `/api/digest` and offers a reload when a skill install
or an HMR swap changes the toolset underneath you.

## The live panel, and why it is a separate server

The HMR loop has its own server — `sk live serve`, the preview panel with the
frame viewer, error overlay and in-page REPL. The two are cross-linked but
never merged:

```bash
sk dev                     # one server: panel at /, catalog at /tools
sk dev --two-servers       # two servers, cross-linked (ADR-0017 mode)
```

The default merges them onto one port. That is a real trade: mounted, the
catalog's read-only-ness is a guard clause plus
[tests](../tests/test_viz_mount.py) rather than the absence of any write verb,
and **anything that can reach `/tools` can reach the REPL**, because they are
one bind. Keep it on loopback. If you need a catalog you can expose safely,
use `sk export viz-html` or the standalone `sk viz --serve` - both unchanged.
[ADR-0018](adr/0018-mount-the-catalog-in-the-panel.md) has the full comparison.

or wire them by hand:

```bash
sk live serve                                  # panel on :8010
sk viz --serve --panel http://127.0.0.1:8010/  # catalog links to it
```

The panel can also link back, via `live.serve {"viz_url": "..."}`. Each
surfaces its peer in `/healthz` and `/version` respectively.

They stay separate because `POST /repl` on the panel **executes code**, while
this catalog has no write verb at all — not "no write route", no `do_POST` on
the handler class. Mounting one into the other would trade a structural
guarantee for a routing-table promise. The reasoning is
[ADR-0017](adr/0017-two-servers-one-viewport.md).

The link is a link and nothing more: neither server fetches, proxies or
iframes the other, and no link is rendered unless you pass one, so the
offline page and `sk export viz-html` output are unchanged.

## Security posture

- **No mutating route exists.** The server has no `do_POST`/`PUT`/`DELETE`/
  `PATCH` handler at all — absent, not disabled. Binds loopback unless `--host`
  says otherwise.
- **Secrets never render.** Schemas and examples pass through the same redaction
  as the external exporters; `secret_args` properties are dropped entirely; the
  ledger overlay redacts args and previews. Tests assert no credential-shaped
  string survives into the page.
- **Hostile manifests cannot inject markup.** The embedded model escapes `<`,
  `>`, `&`, U+2028/9, so a tool whose description contains `</script>` cannot
  break out of the payload block.
- **Nothing is mutated.** Building, serving and overlaying leave the registry's
  digest and the ledger's bytes unchanged.

## The model

`schemas/viz-model.schema.json` is the contract. Nodes carry the flattened
manifest plus `advertised`, `withheld_by`, the gate receipt and (with `--runtime`)
measurements. Edges are typed: `see_also`, `same_capability`, `provides`,
`anti_pattern`. `model_digest` hashes the document minus the clock, so drift is
detectable and two identical toolsets compare equal.

Phase-by-phase build notes, including what was rejected and why, are in
[`TOOL-VISUALIZER-PLAN.md`](TOOL-VISUALIZER-PLAN.md); the design decision is
[ADR 0016](adr/0016-visualizer-is-a-projection.md).
