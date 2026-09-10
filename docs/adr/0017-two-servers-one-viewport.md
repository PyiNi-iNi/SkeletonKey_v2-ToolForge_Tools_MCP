# ADR 0017: Two servers, one viewport

Date: 2026-09-09
Status: accepted
Affects: `skeletonkey/viz/server.py`, `skeletonkey/viz/render.py`,
`skeletonkey/live/panel.py`, `skeletonkey/live/tools.py` (`live.serve`),
`skeletonkey/cli.py` (`sk viz --panel`), `tests/test_viewport_link.py`,
`docs/TOOL-VISUALIZER.md`, `docs/LIVE-HMR.md`

## Context

The repo now has two local HTTP servers that a developer wants open at the
same time, in the same browser, while working:

* **The live panel** (`skeletonkey/live/panel.py`, ADR-0011). Frame viewer,
  error overlay, scene graph, agent debugger, and an in-page REPL console.
  `POST /repl` **executes arbitrary Python in the running program**. It also
  has `POST /api/control` (start/stop/patch/render/snapshot). It is a
  read-write surface by its nature — that is the entire point of an HMR loop.

* **The tool catalog** (`skeletonkey/viz/server.py`, ADR-0016). A read-only
  projection of the registry: what tools exist, which are advertised, why the
  withheld ones are withheld, what they cost. It has no `do_POST`, no
  `do_PUT`, no `do_DELETE`, no `do_PATCH` — the *class* cannot be made to
  mutate anything, which is a structural guarantee rather than a policy one.

Having two URLs on two ports is a real papercut. The obvious fix — mount the
catalog into the panel, one server, one port, tabs across the top — is the
wrong fix, and it is worth writing down exactly why so nobody "simplifies" it
later.

## Decision

**Two servers. One viewport. Cross-linked by URL, never merged.**

The panel's header gains a `tools` link when it knows a catalog URL; the
catalog's header gains a `live panel →` link when it knows a panel URL. Each
learns the other's URL only by being told:

```bash
sk live serve                       # panel on :8010
sk viz --serve --panel http://127.0.0.1:8010/
```

and symmetrically `live.serve {"viz_url": "..."}`. Both surface the peer URL
in their status payloads (`/healthz`, `/version`) so a wrapper can discover
the pair.

## Why not one server

Merging would take the catalog's strongest property and throw it away.

Today "can this page mutate anything?" is answered by reading the handler
class and finding no write verbs. That is checkable in a code review by a
person who does not understand the codebase, it cannot rot, and it does not
depend on the correctness of a router. Mounting the catalog into the panel
would replace that with "the router does not currently dispatch a POST to a
viz path" — a property that holds only as long as every future edit to the
routing table keeps it holding.

The two surfaces also have genuinely different exposure stories. The panel
must stay loopback-bound (or run with `panel_repl=false`) because remote code
execution is a route on it. The catalog is safe to expose much more freely —
it is a projection of public tool metadata, and V6 already ships it as a
static file via `sk export viz-html`. Fusing them would drag the catalog up to
the panel's threat level for a cosmetic gain, and would make "just show
someone the toolset" require standing up a code-execution endpoint.

Finally, they have different lifetimes. The panel is bound to a `LiveManager`
and its programs; the catalog is a pure function of the registry and can be
served with no live session at all. One server would mean one lifetime, and
the catalog would inherit the panel's failure modes.

## Consequences

1. **The link is a string, and only a string.** Neither server fetches,
   proxies, embeds or iframes the other. `panel_url`/`viz_url` are rendered
   into an `href` and nothing else. Enforced by `tests/test_viewport_link.py`.
2. **The catalog gains no write verb.** The existing structural test that
   asserts the handler class has no `do_POST`/`do_PUT`/`do_DELETE`/`do_PATCH`
   continues to hold with a panel link present.
3. **The link is escaped like any other untrusted string.** A URL arriving
   from config or CLI is `_esc`'d into the header; a `javascript:` URL is a
   known hazard of this shape and is tested.
4. **Absent by default.** No link is rendered unless a URL was supplied, so
   the offline `file://` page and `sk export viz-html` output are unchanged —
   byte-identical to V6 when no peer is configured.
5. **Discovery is the caller's job.** Neither server scans ports or guesses at
   its peer. Auto-discovery would mean one server probing local ports, which
   is exactly the behaviour a security review should flag.

## Options considered

1. **Mount the catalog into the panel** (one port). Rejected above: forfeits
   the structural read-only guarantee for a cosmetic gain.
2. **Reverse-proxy the panel from the catalog.** Rejected: the catalog would
   need an outbound HTTP client and would become a confused deputy — a
   read-only server forwarding writes.
3. **A third "shell" server that frames both in iframes.** Rejected: a third
   lifetime and a third bind to reason about, and the panel's REPL in an
   iframe raises its own set of questions. Two tabs is not a problem worth a
   third server.
4. **Cross-link by URL** — chosen. Costs one string on each side.

## Costs

Two ports to remember, and the developer must pass each URL to the other side
to get the links. Auto-discovery would remove that friction, and we are
explicitly declining it (consequence 5). If the friction proves real, the
right fix is a single supervising command that starts both and tells each
about the other — not a probe, and not a merge.
