# ADR 0018: Mount the tool catalog inside the live panel

Date: 2026-09-09
Status: accepted
Supersedes: [ADR-0017](0017-two-servers-one-viewport.md) (partially — the
two-server arrangement remains available and is still the stronger option)
Affects: `skeletonkey/viz/mount.py` (new), `skeletonkey/viz/render.py`
(`api_base`), `skeletonkey/live/panel.py`, `skeletonkey/live/tools.py`
(`live.serve mount_tools`), `skeletonkey/cli.py` (`sk dev --two-servers`),
`tests/test_viz_mount.py`, `docs/TOOL-VISUALIZER.md`, `docs/LIVE-HMR.md`

## Context

ADR-0017 kept the live panel and the read-only tool catalog as two servers,
cross-linked by URL, and argued the split was worth a second port. The
operator's judgement is that one port and one URL is worth more than the
guarantee the split was protecting, in the environments this actually runs in
— a dev box, or a sandbox where every exposed port costs a proxy rule.

That is a legitimate trade and this ADR implements it. It also records, in
full, what was given up, because the property being traded away was the single
strongest security claim in the visualizer and its loss should not be
discovered later by someone reading the code and assuming it still holds.

## What we had

The catalog's handler class had no `do_POST`, `do_PUT`, `do_DELETE` or
`do_PATCH`. "Can this surface mutate anything?" was answered by *absence*:

* checkable in seconds by a reviewer who does not know the codebase,
* impossible to undo by accident, since re-introducing a write meant adding a
  method with an obvious name,
* independent of the correctness of any router, allowlist or guard clause.

## What we have now

The panel serves the catalog under `/tools`. The panel has `do_POST`, because
`POST /repl` executes arbitrary Python — that is the entire point of an HMR
loop. So the absence argument is gone, and the guarantee is now a *guard*:

1. **One explicit allowlist.** `viz/mount.py:ROUTES` is the complete surface,
   as data. A reviewer reads one tuple instead of tracing control flow.
2. **The mount refuses unsafe methods itself.** `VizMount.dispatch()` checks
   the method and 405s anything outside `{GET, HEAD}` — it does not trust the
   host to have called it only from a read verb. A host that wires it into
   `do_POST` by mistake still cannot write through it.
3. **The mount holds no handle to anything mutable.** It is constructed with a
   zero-argument callable returning a registry, so it cannot reach the engine,
   the toolkit, the live manager or the REPL. There is no object graph to walk
   toward a write even if a future route is added carelessly.
4. **The host fences the prefix first.** `Panel.do_POST` refuses anything the
   mount `owns()` before any route matching runs, so a path that resolves
   toward `/repl` fails closed rather than being forwarded.
5. **Tests are the enforcement.** `tests/test_viz_mount.py` covers every write
   verb against eight mount paths, four traversal shapes (including encoded
   ones, checked over a raw socket because `urllib` normalises away the
   interesting cases), the lookalike prefix `/toolsX`, and the no-mutable-
   handles property.

## The honest comparison

| | Two servers (0017) | Mounted (0018) |
|---|---|---|
| Read-only enforced by | absence of the methods | a guard clause + tests |
| Survives a careless refactor | yes, structurally | only if the tests are kept |
| Verifiable without reading tests | yes | no |
| Ports to expose | 2 | 1 |
| Catalog exposable without exposing RCE | yes | **no** |

The last row is the one with teeth. On a merged server, anything that can
reach `/tools` can reach `/repl`; they are one bind, one origin, one firewall
rule. Previously you could hand someone the catalog and expose no code
execution at all.

## Consequences

1. **`sk dev` mounts by default**; `sk dev --two-servers` restores the 0017
   arrangement, which remains supported and is still the stronger posture.
   `sk viz --serve` also still runs the standalone, structurally read-only
   server — that code path is not deleted.
2. **Do not expose a mounted panel beyond loopback by default.** This was
   already true of the panel (`/repl` and `/api/control` mutate live state) and
   is now also true of the catalog. `panel_repl = false` refuses both mutation
   routes, but any remote opt-in still requires `allow_remote`, a 32-byte auth
   token, an origin policy, and an accurate `public_base_url` when the bind is
   wildcarded.
3. **`tests/test_viz_mount.py` is load-bearing.** Weakening or skipping it
   silently removes the property, with nothing in the class shape left to
   notice. It says so at the top of the file.
4. **The page needs an explicit API base.** Relative `api/digest` resolves
   differently at `/tools` and `/tools/`; `render_html(api_base=...)` removes
   that footgun. The unmounted page is unchanged.
5. **The offline page is unaffected.** `sk export viz-html` and
   `sk viz -o tools.html` do not go through the mount.

## Options considered

1. **Keep two servers** (status quo, ADR-0017). Rejected by the operator: the
   second port is a real cost in sandboxed and proxied environments.
2. **Mount, with the guarantee re-established by guard + tests** — chosen.
3. **Mount the panel inside the catalog instead.** Rejected: it inverts the
   trust relationship, putting a code-execution route inside the surface we
   describe as read-only. Strictly worse framing for the same topology.
4. **Serve the catalog as a static file from the panel.** Tempting — a static
   file genuinely cannot mutate. Rejected because it loses the live model:
   drift detection, the runtime overlay and `/api/*` all need a live registry.
   `sk export viz-html` already covers the static case for anyone who wants
   it, and *that* remains the option to reach for when exposure matters.

## Costs

The catalog can no longer be exposed without also exposing code execution, and
its read-only-ness is now a property maintained by vigilance rather than by
construction. If a future change needs the strong version back — publishing a
catalog to a shared host, say — use `sk viz --serve` or `sk export viz-html`
rather than reaching for the mount.
