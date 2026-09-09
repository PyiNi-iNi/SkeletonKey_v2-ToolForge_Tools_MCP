"""V1: the catalog page - one self-contained HTML file for the whole toolset.

`render_html(model)` returns a single string with the CSS, the JS and the model
itself inlined. No network fetch, no CDN, no build step: the file works from a
`file://` URL on a laptop with the wifi off, which is the point - it is the
artifact you mail to someone, commit to a repo, or attach to a review.

The page is a *view over the V0 model* and holds no facts of its own. Every
number it shows is read from the embedded document at load time, so a page and
a `sk viz --json` taken from the same registry can never disagree.

Layout: a header of aggregates, a left rail of groups, a filter bar, a card
grid, and a detail drawer. Progressive enhancement is deliberate - the cards are
rendered into the HTML as real markup rather than built by JS, so a browser with
scripting disabled still gets the complete, readable catalog (V3's graph will
degrade to exactly this).
"""

from __future__ import annotations

import html
from typing import Any

from skeletonkey.core.util import stable_json

from .model import VizModel

# Risk classes get a colour each; anything unknown falls back to the neutral one.
_RISK_COLOURS = {
    "none": "#8b949e",
    "read": "#58a6ff",
    "write": "#d29922",
    "destructive": "#f85149",
    "network": "#a371f7",
    "privileged": "#ff7b72",
}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_for_script(payload: Any) -> str:
    """Embed JSON inside a <script> without letting it close the tag.

    `</script>` anywhere in a tool description would otherwise end the block and
    spill the model into the document as markup. Escaping the sequences below is
    the whole defence, and it is tested.
    """
    text = stable_json(payload)
    return (text.replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026").replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))


def _num(n: int | float) -> str:
    return f"{n:,}".replace(",", "\u2009")   # thin space: readable, no locale


# ----------------------------------------------------------------- fragments

def _stat(label: str, value: str, *, tone: str = "") -> str:
    cls = f" class={tone}" if tone else ""
    return (f'<div class="stat"><div class="stat-v"{cls}>{_esc(value)}</div>'
            f'<div class="stat-k">{_esc(label)}</div></div>')


def _badges(node: Any) -> str:
    out = [f'<span class="badge risk-{_esc(node.risk)}">{_esc(node.risk)}</span>',
           f'<span class="badge tier">{_esc(node.tier)}</span>']
    if node.destructive:
        out.append('<span class="badge danger">destructive</span>')
    if node.reversible:
        out.append('<span class="badge ok">reversible</span>')
    if node.stateful != "none":
        out.append(f'<span class="badge">stateful:{_esc(node.stateful)}</span>')
    if node.open_world:
        out.append('<span class="badge">open-world</span>')
    if not node.parallel_safe:
        out.append('<span class="badge">serial</span>')
    if node.advertised:
        out.append('<span class="badge ok">advertised</span>')
    else:
        out.append(f'<span class="badge warn">withheld: {_esc(node.withheld_by)}</span>')
    return "".join(out)


def _card(node: Any) -> str:
    """One tool card. `data-*` attributes are what the client-side filter reads."""
    haystack = " ".join([node.id, node.title, node.summary, node.capability,
                         " ".join(node.tags)]).lower()
    return (
        f'<article class="card{"" if node.advertised else " withheld"}" '
        f'data-id="{_esc(node.id)}" data-group="{_esc(node.group)}" '
        f'data-risk="{_esc(node.risk)}" data-tier="{_esc(node.tier)}" '
        f'data-adv="{"1" if node.advertised else "0"}" '
        f'data-destructive="{"1" if node.destructive else "0"}" '
        f'data-examples="{node.example_count}" '
        f'data-text="{_esc(haystack)}" tabindex="0">'
        f'<h3>{_esc(node.title)}</h3>'
        f'<code class="tid">{_esc(node.id)}</code>'
        f'<p class="sum">{_esc(node.summary)}</p>'
        f'<div class="badges">{_badges(node)}</div>'
        f'<div class="foot"><span>{_esc(node.capability)}</span>'
        f'<span>{_num(node.typical_latency_ms)} ms &middot; {_num(node.token_cost)} tok</span>'
        f'</div></article>'
    )


def _group_rail(model: VizModel) -> str:
    rows = ['<button class="grp active" data-group="*">'
            f'<span>all tools</span><span class="n">{len(model.nodes)}</span></button>']
    for g in sorted(model.groups, key=lambda g: (-g["tools"], g["name"])):
        rows.append(
            f'<button class="grp" data-group="{_esc(g["name"])}">'
            f'<span>{_esc(g["name"])}</span>'
            f'<span class="n">{g["tools"]}</span></button>'
        )
    return "".join(rows)


def _header(model: VizModel, title: str) -> str:
    agg = model.aggregates
    host = model.host
    where = " ".join(x for x in (host.get("os"), host.get("arch")) if x) or "unknown host"
    withheld_note = ""
    if agg["withheld"]:
        causes = ", ".join(f"{v}&nbsp;{_esc(k)}" for k, v in agg["withheld_by"].items())
        withheld_note = f'<span class="dim"> ({causes})</span>'
    return (
        f'<header><div class="hrow"><h1>{_esc(title)}</h1>'
        f'<span class="dim">{_esc(where)} &middot; tier {_esc(model.snapshot["tier"])} '
        f'&middot; digest <code>{_esc(model.snapshot["digest"])}</code></span></div>'
        f'<div class="stats">'
        f'{_stat("registered", _num(agg["registered"]))}'
        f'{_stat("advertised here", _num(agg["advertised"]), tone="ok")}'
        f'{_stat("withheld", _num(agg["withheld"]), tone="warn")}'
        f'{_stat("groups", _num(agg["groups"]))}'
        f'{_stat("capabilities", _num(agg["capabilities"]))}'
        f'{_stat("destructive", _num(agg["destructive"]), tone="bad")}'
        f'{_stat("advertised tokens", _num(model.snapshot["tokens"]))}'
        f'{_stat("relations", _num(len(model.edges)))}'
        f'</div><p class="sub">Every registered tool is listed, advertised or not'
        f'{withheld_note}. Click a card for the schema, examples and the gate receipt.</p>'
        f'</header>'
    )


# --------------------------------------------------------------------- page

def render_html(model: VizModel, *, title: str = "SkeletonKey toolset",
                embed_model: bool = True, live: bool = False,
                poll_ms: int = 2000) -> str:
    """Render the whole model as one standalone HTML document.

    `live=True` adds the drift banner: the page polls `/api/digest` and, when
    the toolset changes underneath it, says so instead of quietly showing a
    stale catalog. The served page (V2) is otherwise byte-for-byte the file you
    get from `sk viz -o tools.html` - one renderer, not two.
    """
    doc = model.to_json()
    doc["model_digest"] = model.digest()
    cards = "".join(_card(n) for n in sorted(model.nodes, key=lambda n: (n.group, n.id)))
    payload = _json_for_script(doc) if embed_model else "null"
    live_cfg = _json_for_script({"live": bool(live), "pollMs": int(poll_ms),
                                 "digest": doc["model_digest"]})

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
{_CSS}
</style>
</head>
<body>
<div id="drift" hidden>The toolset changed underneath this page.
  <button id="reload">reload</button>
  <span id="drift-what" class="dim"></span>
</div>
{_header(model, title)}
<div class="layout">
  <nav id="rail">{_group_rail(model)}</nav>
  <main>
    <div id="filters">
      <input id="q" type="search" placeholder="search id, title, capability, tag&hellip;  ( / )"
             autocomplete="off" spellcheck="false">
      <label><input type="checkbox" id="f-adv"> advertised only</label>
      <label><input type="checkbox" id="f-destructive"> destructive</label>
      <label><input type="checkbox" id="f-examples"> has examples</label>
      <select id="f-risk"><option value="*">any risk</option>
        {"".join(f'<option value="{_esc(r)}">{_esc(r)}</option>' for r in sorted(model.aggregates["risk"]))}
      </select>
      <select id="f-tier"><option value="*">any tier</option>
        <option value="core">core</option><option value="task">task</option>
        <option value="full">full</option>
      </select>
      <span id="count" class="dim"></span>
    </div>
    <div id="grid">{cards}</div>
    <p id="empty" hidden>Nothing matches that filter.</p>
  </main>
</div>
<aside id="drawer" hidden aria-label="tool detail"><button id="close">close &times;</button>
  <div id="detail"></div>
</aside>
<div id="scrim" hidden></div>
<script id="viz-model" type="application/json">{payload}</script>
<script id="viz-live" type="application/json">{live_cfg}</script>
<script>
{_JS}
</script>
</body>
</html>
"""


_CSS = r"""
:root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3; --dim:#8b949e;
        --accent:#58a6ff; --good:#3fb950; --bad:#f85149; --warn:#d29922; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:13px/1.55 ui-monospace,
       SFMono-Regular,Menlo,Consolas,monospace; }
code { font-family:inherit; }
.dim { color:var(--dim); }
header { padding:14px 18px; background:var(--panel); border-bottom:1px solid var(--line); }
.hrow { display:flex; gap:14px; align-items:baseline; flex-wrap:wrap; }
h1 { font-size:15px; margin:0; letter-spacing:.02em; }
.stats { display:flex; gap:22px; flex-wrap:wrap; margin-top:10px; }
.stat-v { font-size:19px; } .stat-v.ok{color:var(--good);} .stat-v.warn{color:var(--warn);}
.stat-v.bad{color:var(--bad);}
.stat-k { color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
.sub { color:var(--dim); margin:10px 0 0; font-size:12px; }
.layout { display:flex; align-items:flex-start; }
#rail { width:190px; flex:none; padding:12px 8px; border-right:1px solid var(--line);
        position:sticky; top:0; max-height:100vh; overflow:auto; }
.grp { display:flex; justify-content:space-between; width:100%; gap:8px; background:none;
       border:1px solid transparent; border-radius:6px; color:var(--fg); font:inherit;
       padding:4px 8px; cursor:pointer; text-align:left; }
.grp:hover { border-color:var(--line); }
.grp.active { background:#1f6feb22; border-color:var(--accent); color:var(--accent); }
.grp .n { color:var(--dim); }
main { flex:1; min-width:0; padding:12px 16px 40px; }
#filters { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
input,select,button { background:#0d1117; color:var(--fg); border:1px solid var(--line);
       border-radius:6px; font:inherit; padding:4px 8px; }
#q { flex:1; min-width:220px; }
#filters label { color:var(--dim); display:flex; gap:5px; align-items:center; cursor:pointer; }
#grid { display:grid; gap:10px; grid-template-columns:repeat(auto-fill,minmax(268px,1fr)); }
.card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
        padding:10px 12px; cursor:pointer; }
.card:hover,.card:focus { border-color:var(--accent); outline:none; }
.card.withheld { opacity:.62; border-style:dashed; }
.card h3 { margin:0; font-size:13px; font-weight:600; }
.tid { color:var(--accent); font-size:11px; }
.sum { margin:6px 0 8px; color:var(--dim); font-size:12px; }
.badges { display:flex; gap:4px; flex-wrap:wrap; }
.badge { font-size:10px; padding:0 6px; border:1px solid var(--line); border-radius:9px;
         color:var(--dim); white-space:nowrap; }
.badge.ok{color:var(--good);border-color:#238636;} .badge.warn{color:var(--warn);border-color:#9e6a03;}
.badge.danger{color:var(--bad);border-color:#8b2c26;} .badge.tier{color:var(--fg);}
.badge.risk-read{color:var(--accent);} .badge.risk-write{color:var(--warn);}
.badge.risk-destructive{color:var(--bad);} .badge.risk-none{color:var(--dim);}
.foot { display:flex; justify-content:space-between; gap:8px; margin-top:8px;
        color:var(--dim); font-size:11px; }
#drawer { position:fixed; top:0; right:0; width:min(620px,94vw); height:100vh; overflow:auto;
          background:var(--panel); border-left:1px solid var(--line); padding:16px 18px 60px;
          z-index:3; box-shadow:-12px 0 40px rgba(0,0,0,.5); }
#scrim { position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:2; }
#close { position:absolute; top:10px; right:12px; }
#detail h2 { font-size:15px; margin:0 0 2px; }
#detail h4 { margin:16px 0 6px; font-size:11px; text-transform:uppercase;
             letter-spacing:.07em; color:var(--dim); }
#detail pre { background:#0d1117; border:1px solid var(--line); border-radius:6px;
              padding:8px 10px; overflow:auto; font-size:11.5px; margin:0 0 8px; }
#detail table { border-collapse:collapse; width:100%; font-size:12px; }
#detail td { padding:2px 8px 2px 0; vertical-align:top; }
#detail td:first-child { color:var(--dim); white-space:nowrap; }
#detail ul { margin:0; padding-left:18px; }
#detail a { color:var(--accent); cursor:pointer; text-decoration:none; }
#detail a:hover { text-decoration:underline; }
.gate-bad { color:var(--warn); }
#empty { color:var(--dim); }
#drift { position:sticky; top:0; z-index:4; padding:8px 14px; background:#9e6a0322;
         border-bottom:1px solid var(--warn); color:var(--warn); display:flex;
         gap:10px; align-items:center; flex-wrap:wrap; }
#drift button { color:var(--fg); }
@media (prefers-reduced-motion:no-preference) { .card { transition:border-color .12s; } }
@media (max-width:760px) { #rail { display:none; } }
"""


_JS = r"""
(function () {
  "use strict";
  var el = document.getElementById("viz-model");
  var MODEL = el ? JSON.parse(el.textContent) : null;
  var BY_ID = {};
  if (MODEL) { MODEL.nodes.forEach(function (n) { BY_ID[n.id] = n; }); }

  var cards = Array.prototype.slice.call(document.querySelectorAll(".card"));
  var q = document.getElementById("q");
  var fAdv = document.getElementById("f-adv");
  var fDes = document.getElementById("f-destructive");
  var fEx = document.getElementById("f-examples");
  var fRisk = document.getElementById("f-risk");
  var fTier = document.getElementById("f-tier");
  var count = document.getElementById("count");
  var empty = document.getElementById("empty");
  var group = "*";

  function apply() {
    var needle = q.value.trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (c) {
      var d = c.dataset;
      var ok = (group === "*" || d.group === group) &&
        (!needle || d.text.indexOf(needle) !== -1) &&
        (!fAdv.checked || d.adv === "1") &&
        (!fDes.checked || d.destructive === "1") &&
        (!fEx.checked || d.examples !== "0") &&
        (fRisk.value === "*" || d.risk === fRisk.value) &&
        (fTier.value === "*" || d.tier === fTier.value);
      c.hidden = !ok;
      if (ok) { shown++; }
    });
    count.textContent = shown + " of " + cards.length + " tools";
    empty.hidden = shown !== 0;
  }

  [q, fAdv, fDes, fEx, fRisk, fTier].forEach(function (c) {
    c.addEventListener("input", apply);
  });
  document.querySelectorAll(".grp").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll(".grp").forEach(function (o) { o.classList.remove("active"); });
      b.classList.add("active");
      group = b.dataset.group;
      apply();
    });
  });

  // ---- detail drawer ------------------------------------------------------
  var drawer = document.getElementById("drawer");
  var scrim = document.getElementById("scrim");
  var detail = document.getElementById("detail");

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function row(k, v) { return v || v === 0 || v === false
    ? "<tr><td>" + esc(k) + "</td><td>" + esc(v) + "</td></tr>" : ""; }
  function pre(obj) { return "<pre>" + esc(JSON.stringify(obj, null, 2)) + "</pre>"; }

  function gateBlock(n) {
    if (n.advertised) {
      return "<h4>exposure</h4><p>Advertised on this host.</p>";
    }
    var out = "<h4>why you cannot call this here</h4><p class='gate-bad'>withheld: " +
      esc(n.withheld_by) + "</p><ul>";
    var lines = (n.gate.reasons || []).concat(n.gate.unmet || []);
    if (n.budget_drop) { lines.push(n.budget_drop); }
    if (!lines.length && n.hidden_reason) { lines.push(n.hidden_reason); }
    lines.forEach(function (r) { out += "<li>" + esc(r) + "</li>"; });
    return out + "</ul>";
  }

  function relations(n) {
    if (!MODEL) { return ""; }
    var mine = MODEL.edges.filter(function (e) {
      return e.source === n.id || e.target === n.id;
    });
    if (!mine.length) { return ""; }
    var by = {};
    mine.forEach(function (e) {
      var other = e.source === n.id ? e.target : e.source;
      (by[e.kind] = by[e.kind] || []).push({ id: other, label: e.label || "" });
    });
    var out = "<h4>relations</h4>";
    Object.keys(by).sort().forEach(function (kind) {
      out += "<p>" + esc(kind) + ": " + by[kind].map(function (r) {
        return "<a data-goto='" + esc(r.id) + "'>" + esc(r.id) + "</a>";
      }).join(", ") + "</p>";
    });
    return out;
  }

  function open(id) {
    var n = BY_ID[id];
    if (!n) { return; }
    var h = "<h2>" + esc(n.title) + "</h2><code class='tid'>" + esc(n.id) + "</code>";
    h += "<p>" + esc(n.description || n.summary) + "</p>";
    h += "<h4>facts</h4><table>" +
      row("group", n.group) + row("capability", n.capability) +
      row("provider", n.provider) + row("tier", n.tier) + row("risk", n.risk) +
      row("source", n.source) + row("version", n.version) +
      row("mutating", n.mutating) + row("destructive", n.destructive) +
      row("reversible", n.reversible) + row("idempotent", n.idempotent) +
      row("parallel safe", n.parallel_safe) + row("stateful", n.stateful) +
      row("approval", n.approval) +
      row("typical latency", n.typical_latency_ms + " ms") +
      row("typical output", n.typical_output_bytes + " bytes") +
      row("timeout", n.timeout_s + " s") + row("token cost", n.token_cost) +
      row("platforms", (n.platforms || []).join(", ")) +
      row("tags", (n.tags || []).join(", ")) +
      "</table>";
    h += gateBlock(n);
    if ((n.requires || []).length || (n.requires_any || []).length) {
      h += "<h4>requirements</h4>";
      (n.requires || []).forEach(function (r) {
        h += "<p>needs " + esc(r.kind) + ":" + esc(r.name) + "</p>";
      });
      if ((n.requires_any || []).length) {
        h += "<p>any of: " + (n.requires_any).map(function (r) {
          return esc(r.kind + ":" + r.name);
        }).join(", ") + "</p>";
      }
    }
    if (n.input_schema && Object.keys(n.input_schema).length) {
      h += "<h4>input schema</h4>" + pre(n.input_schema);
    }
    if (n.output_schema) { h += "<h4>output schema</h4>" + pre(n.output_schema); }
    if ((n.examples || []).length) {
      h += "<h4>examples</h4>";
      n.examples.forEach(function (ex) { h += pre(ex); });
    }
    if ((n.anti_patterns || []).length) {
      h += "<h4>do not</h4><ul>" + n.anti_patterns.map(function (a) {
        return "<li>" + esc(a) + "</li>";
      }).join("") + "</ul>";
    }
    h += relations(n);
    detail.innerHTML = h;
    detail.querySelectorAll("[data-goto]").forEach(function (a) {
      a.addEventListener("click", function () { open(a.dataset.goto); });
    });
    drawer.hidden = false; scrim.hidden = false;
    drawer.scrollTop = 0;
    if (location.hash.slice(1) !== id) { history.replaceState(null, "", "#" + id); }
  }
  function close() {
    drawer.hidden = true; scrim.hidden = true;
    history.replaceState(null, "", location.pathname + location.search);
  }

  cards.forEach(function (c) {
    c.addEventListener("click", function () { open(c.dataset.id); });
    c.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(c.dataset.id); }
    });
  });
  document.getElementById("close").addEventListener("click", close);
  scrim.addEventListener("click", close);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { close(); }
    if (e.key === "/" && document.activeElement !== q) { e.preventDefault(); q.focus(); }
  });

  apply();
  if (location.hash.length > 1) { open(decodeURIComponent(location.hash.slice(1))); }

  // ---- drift watch (V2: only when served) ---------------------------------
  var cfgEl = document.getElementById("viz-live");
  var CFG = cfgEl ? JSON.parse(cfgEl.textContent) : null;
  if (CFG && CFG.live && window.fetch) {
    var banner = document.getElementById("drift");
    var what = document.getElementById("drift-what");
    document.getElementById("reload").addEventListener("click", function () {
      location.reload();
    });
    var misses = 0;
    setInterval(function () {
      fetch("api/digest", { cache: "no-store" }).then(function (r) {
        return r.json();
      }).then(function (d) {
        misses = 0;
        if (d.model_digest && d.model_digest !== CFG.digest) {
          var before = MODEL ? MODEL.aggregates.registered : "?";
          what.textContent = "was " + before + " tools, now " + d.registered +
            " (" + d.advertised + " advertised)";
          banner.hidden = false;
        }
      }).catch(function () {
        // The server went away: say so once, then stop shouting about it.
        if (++misses === 3) {
          what.textContent = "the viz server is no longer answering";
          banner.hidden = false;
        }
      });
    }, CFG.pollMs || 2000);
  }
}());
"""
