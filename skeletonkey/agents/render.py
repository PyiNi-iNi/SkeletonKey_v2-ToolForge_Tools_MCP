"""Dependency-free purpose-agent workspace UI.

The page is a server-rendered projection of :class:`AgentModel`.  It has no
execution form and no write route: the visible "actions" are delegation
recipes, while real execution is created through ``create_agent`` and the
existing subagent boundary.
"""

from __future__ import annotations

import html
from typing import Any

from skeletonkey.core.util import stable_json

from .model import AgentModel, PurposeAgent


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_for_script(value: Any) -> str:
    return (stable_json(value).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _badge(text: str, cls: str = "") -> str:
    return f'<span class="badge {cls}">{_esc(text)}</span>'


def _tool_row(tool: Any) -> str:
    status_label = {
        "ready": "ready",
        "approval": "approval",
        "read-only": "agent read-only",
        "host-read-only": "host read-only",
        "tier": "tier gated",
        "withheld": "host withheld",
    }.get(tool.status, tool.status)
    status_cls = "ready" if tool.status == "ready" else "gated"
    reason = f' title="{_esc(tool.reason)}"' if tool.reason else ""
    return (
        f'<li class="tool-row {status_cls}" data-tool="{_esc(tool.id)}"{reason}>'
        f'<span class="status-dot" aria-hidden="true"></span>'
        f'<code>{_esc(tool.id)}</code>'
        f'<span class="tool-cap">{_esc(tool.capability)}</span>'
        f'{_badge(tool.risk, "risk-" + tool.risk)}'
        f'{_badge(status_label, "status")}'
        f'</li>'
    )


def _action(action: dict[str, Any]) -> str:
    caps = " · ".join(action.get("capabilities") or [])
    return (
        f'<li class="action">'
        f'<div class="action-top"><strong>{_esc(action.get("title", action.get("id", "action")))}</strong>'
        f'{_badge(action.get("confirmation", "policy"), "confirm")}</div>'
        f'<p>{_esc(action.get("objective", ""))}</p>'
        f'<code>{_esc(caps)}</code>'
        f'</li>'
    )


def _agent_card(agent: PurposeAgent) -> str:
    mode = "read-only" if agent.read_only else "autonomous · policy-gated"
    mode_cls = "quiet" if agent.read_only else "hot"
    text = " ".join([agent.id, agent.title, agent.purpose, agent.mission,
                      " ".join(t.id for t in agent.tools)])
    tools = "".join(_tool_row(t) for t in agent.tools)
    actions = "".join(_action(a) for a in agent.actions)
    groups = " ".join(_badge(g, "group") for g in agent.groups)
    return f'''
      <article class="agent-card" id="agent-{_esc(agent.id)}" data-agent="{_esc(agent.id)}"
               data-text="{_esc(text.lower())}">
        <div class="agent-head">
          <div class="agent-mark">{_esc(agent.id.split("-")[0][:2].upper())}</div>
          <div>
            <div class="eyebrow">purpose agent / {agent.tier} tier</div>
            <h2>{_esc(agent.title)}</h2>
            <code class="agent-id">{_esc(agent.id)}</code>
          </div>
          <div class="agent-mode {_esc(mode_cls)}">{_esc(mode)}</div>
        </div>
        <p class="purpose">{_esc(agent.purpose)}</p>
        <p class="mission"><span>MISSION</span>{_esc(agent.mission)}</p>
        <div class="agent-metrics">
          <div><strong>{len(agent.tools)}</strong><span>mapped tools</span></div>
          <div><strong>{len(agent.available_tools)}</strong><span>ready now</span></div>
          <div><strong>{len(agent.gated_tools)}</strong><span>gated / withheld</span></div>
          <div><strong>{agent.max_calls}</strong><span>call budget</span></div>
        </div>
        <div class="agent-columns">
          <section>
            <div class="section-label">specific actions</div>
            <ul class="actions">{actions}</ul>
          </section>
          <section class="tooling">
            <div class="section-label">tooling contract <span>{groups}</span></div>
            <ul class="tools">{tools}</ul>
          </section>
        </div>
        <footer class="agent-foot">
          <span>limit {agent.max_wall_time_s:g}s · output {agent.max_output_bytes:,} bytes</span>
          <span>{_esc(agent.risk_note)}</span>
        </footer>
      </article>
    '''


def render_agent_html(
    model: AgentModel,
    *,
    title: str = "SkeletonKey agent workspace",
    live: bool = False,
    api_base: str = "/api/agents",
    catalog_url: str | None = None,
) -> str:
    """Render a standalone, offline-capable agent-to-tool workspace."""
    payload = _json_for_script(model.to_json())
    host = model.host
    where = " ".join(x for x in (host.get("os"), host.get("arch")) if x) or "unknown host"
    catalog_link = (
        f'<a class="top-link" href="{_esc(catalog_url)}">tool catalog ↗</a>'
        if catalog_url else ""
    )
    cards = "".join(_agent_card(a) for a in model.agents)
    unassigned = "".join(f"<code>{_esc(t)}</code>" for t in model.unassigned_tools)
    unassigned_block = (
        f'<aside class="unassigned"><strong>Unassigned live tools</strong>'
        f'<span>{unassigned}</span></aside>' if model.unassigned_tools else
        '<aside class="coverage-ok"><strong>Coverage contract passed</strong>'
        '<span>Every live registered tool is mapped to at least one purpose agent.</span></aside>'
    )
    live_cfg = _json_for_script({
        "live": live,
        "apiBase": api_base,
        "digest": model.digest(),
    })
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
:root {{
  color-scheme: dark; --bg:#09111f; --panel:#111d30; --panel2:#14233a;
  --line:#263b59; --text:#e8eef8; --muted:#8ea2be; --accent:#63e6be;
  --accent2:#78a9ff; --amber:#f5c56b; --danger:#ff8585; --shadow:0 24px 80px #02061199;
}}
* {{ box-sizing:border-box }} body {{ margin:0; background:radial-gradient(circle at 72% -10%,#1b3d5a 0,#09111f 42rem); color:var(--text); font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif }}
a {{ color:var(--accent2); text-decoration:none }} code {{ font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace; color:#b9c9e4 }}
.shell {{ max-width:1540px; margin:auto; padding:36px 34px 80px }}
.top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:24px; margin-bottom:32px }}
.kicker,.eyebrow,.section-label {{ color:var(--accent); text-transform:uppercase; letter-spacing:.14em; font-size:10px; font-weight:800 }}
h1 {{ margin:6px 0 8px; font-size:clamp(30px,4vw,58px); line-height:1.02; letter-spacing:-.045em }}
.lede {{ margin:0; max-width:760px; color:var(--muted); font-size:16px }}
.top-link {{ border:1px solid var(--line); padding:9px 13px; border-radius:9px; white-space:nowrap; background:#0d1a2b }}
.contract {{ display:flex; flex-wrap:wrap; gap:8px; margin:20px 0 28px }}
.contract span {{ border:1px solid #2f5670; border-radius:999px; color:#b9d9ea; padding:5px 10px; background:#102738aa; font-size:12px }}
.stats {{ display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:10px; margin-bottom:28px }}
.stat {{ border:1px solid var(--line); background:#0e1a2b; border-radius:12px; padding:13px 15px; box-shadow:var(--shadow) }}
.stat strong {{ display:block; font-size:25px; letter-spacing:-.04em }} .stat span {{ color:var(--muted); font-size:11px }}
.toolbar {{ position:sticky; top:12px; z-index:4; display:flex; gap:10px; padding:10px; margin-bottom:24px; border:1px solid var(--line); background:#0c1728e8; backdrop-filter:blur(12px); border-radius:12px }}
input {{ width:min(560px,100%); color:var(--text); background:#07101d; border:1px solid #355173; padding:11px 14px; border-radius:8px; outline:none }}
input:focus {{ border-color:var(--accent2); box-shadow:0 0 0 3px #78a9ff22 }}
.filter {{ border:1px solid var(--line); border-radius:8px; padding:0 12px; color:var(--muted); background:#101f33 }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(500px,1fr)); gap:18px }}
.agent-card {{ border:1px solid var(--line); border-radius:16px; background:linear-gradient(145deg,#14243a,#0d192a); overflow:hidden; box-shadow:0 16px 45px #02081766; transition:transform .16s,border-color .16s }}
.agent-card:hover {{ transform:translateY(-2px); border-color:#4c7094 }} .agent-card.is-hidden {{ display:none }}
.agent-head {{ display:flex; align-items:center; gap:13px; padding:20px 21px 8px }}
.agent-mark {{ display:grid; place-items:center; width:44px; height:44px; border-radius:12px; color:#07201a; background:linear-gradient(135deg,var(--accent),#84aaff); font-weight:900; letter-spacing:-.08em }}
.agent-head h2 {{ margin:2px 0 0; font-size:21px; letter-spacing:-.03em }} .agent-id {{ color:#7894ba }}
.agent-mode {{ margin-left:auto; border:1px solid; border-radius:999px; padding:4px 8px; font-size:10px; text-transform:uppercase; letter-spacing:.08em; white-space:nowrap }}
.agent-mode.quiet {{ color:#a9c4e5; border-color:#45617e }} .agent-mode.hot {{ color:#ffd891; border-color:#856938; background:#4c381d55 }}
.purpose {{ margin:12px 21px 6px; color:#f5f8ff; font-size:16px; font-weight:650 }} .mission {{ margin:0 21px 18px; color:var(--muted) }}
.mission span {{ color:var(--accent); font-size:10px; font-weight:800; letter-spacing:.1em; margin-right:8px }}
.agent-metrics {{ display:grid; grid-template-columns:repeat(4,1fr); border-top:1px solid var(--line); border-bottom:1px solid var(--line); background:#0a1525aa }}
.agent-metrics div {{ padding:11px 13px; border-right:1px solid var(--line) }} .agent-metrics div:last-child {{ border:0 }}
.agent-metrics strong {{ display:block; font-size:20px; color:#f5f8ff }} .agent-metrics span {{ color:var(--muted); font-size:10px }}
.agent-columns {{ display:grid; grid-template-columns:.82fr 1.18fr; gap:18px; padding:18px 21px 12px }}
.section-label {{ color:#91aac9; margin-bottom:10px }} .section-label span {{ float:right; text-transform:none; letter-spacing:0; font-weight:400 }}
.actions,.tools {{ list-style:none; margin:0; padding:0 }} .actions {{ display:grid; gap:8px }}
.action {{ border-left:2px solid var(--accent); padding:8px 10px; background:#0b1b2a; border-radius:0 8px 8px 0 }}
.action-top {{ display:flex; justify-content:space-between; align-items:center; gap:8px }} .action p {{ color:var(--muted); margin:4px 0 5px; font-size:12px }} .action code {{ color:#7db3d5; font-size:10px }}
.tools {{ max-height:260px; overflow:auto; padding-right:4px }} .tool-row {{ display:grid; grid-template-columns:8px minmax(120px,1fr) minmax(80px,.8fr) auto auto; align-items:center; gap:7px; padding:6px 7px; border-bottom:1px solid #1c2d45 }}
.tool-row:last-child {{ border:0 }} .tool-row code {{ font-size:11px; color:#d1dcf0 }} .tool-cap {{ overflow:hidden; color:#748cac; text-overflow:ellipsis; white-space:nowrap; font-size:10px }}
.status-dot {{ width:7px; height:7px; border-radius:50%; background:var(--danger) }} .tool-row.ready .status-dot {{ background:var(--accent); box-shadow:0 0 9px var(--accent) }}
.badge {{ display:inline-block; border:1px solid #3a506c; border-radius:999px; padding:2px 6px; color:#afc0d8; font-size:9px; line-height:1.25; white-space:nowrap }}
.badge.status {{ color:#ffb5b5; border-color:#684b5a }} .tool-row.ready .badge.status {{ color:#99eacb; border-color:#316a61 }}
.badge.confirm {{ color:#f4c97c; border-color:#765c35 }} .badge.group {{ margin-left:4px; color:#8cb8ff }}
.risk-destructive,.risk-privileged {{ color:#ff9d9d; border-color:#754955 }} .risk-write {{ color:#f4c97c; border-color:#765c35 }} .risk-read,.risk-none {{ color:#9ddacb; border-color:#32655d }}
.agent-foot {{ display:flex; justify-content:space-between; gap:12px; padding:12px 21px 16px; color:#7186a3; font-size:10px; border-top:1px solid #1d304a }}
.agent-foot span:last-child {{ text-align:right; max-width:58% }} .coverage-ok,.unassigned {{ display:flex; justify-content:space-between; gap:14px; border:1px solid #316a61; border-radius:10px; padding:11px 14px; margin:-8px 0 22px; color:#a5ead6; background:#0b292b66 }}
.unassigned {{ border-color:#765c35; color:#f4c97c }} .unassigned span {{ display:flex; flex-wrap:wrap; gap:6px }}
footer.page-foot {{ margin-top:30px; color:#7186a3; font-size:12px; display:flex; justify-content:space-between; gap:12px; border-top:1px solid var(--line); padding-top:16px }}
@media (max-width:760px) {{ .shell {{ padding:24px 14px 50px }} .top {{ display:block }} .top-link {{ display:inline-block; margin-top:16px }} .stats {{ grid-template-columns:repeat(2,1fr) }} .grid {{ grid-template-columns:1fr }} .agent-columns {{ grid-template-columns:1fr }} .agent-mode {{ font-size:8px }} .agent-foot {{ display:block }} .agent-foot span:last-child {{ max-width:none; text-align:left; display:block; margin-top:6px }} }}
@media (prefers-reduced-motion:reduce) {{ .agent-card {{ transition:none }} .agent-card:hover {{ transform:none }} }}
</style>
</head>
<body>
<main class="shell">
  <header class="top">
    <div>
      <div class="kicker">SkeletonKey / purpose-driven autonomy</div>
      <h1>Agent workspace</h1>
      <p class="lede">Every live tool is assigned to a bounded agent purpose. Choose the mission first; the registry, policy gate, and journal decide the tools that agent can actually use.</p>
      <div class="contract"><span>ADR-0019 contract</span><span>projection of live tooling</span><span>no direct UI execution</span><span>policy + ledger enforced</span></div>
    </div>
    {catalog_link}
  </header>
  <section class="stats">
    <div class="stat"><strong>{len(model.agents)}</strong><span>purpose agents</span></div>
    <div class="stat"><strong>{model.tool_count}</strong><span>live registered tools</span></div>
    <div class="stat"><strong>{model.assigned_tool_count}</strong><span>uniquely assigned</span></div>
    <div class="stat"><strong>{sum(len(a.available_tools) for a in model.agents)}</strong><span>ready assignments</span></div>
    <div class="stat"><strong>{_esc(where)}</strong><span>host projection</span></div>
  </section>
  {unassigned_block}
  <div class="toolbar">
    <input id="agent-search" type="search" placeholder="Filter agents, purposes, actions, or tool IDs…" aria-label="Filter agents and tools">
    <select id="agent-filter" class="filter" aria-label="Show agent type"><option value="all">all purposes</option><option value="readonly">read-only</option><option value="autonomous">policy-gated writes</option></select>
  </div>
  <section class="grid" id="agent-grid">{cards}</section>
  <footer class="page-foot"><span>agent digest <code>{_esc(model.digest())}</code> · tool digest <code>{_esc(model.tool_digest)}</code></span><span>Read-only projection; execute through <code>create_agent()</code> and <code>call_as_subagent()</code>.</span></footer>
</main>
<script id="agent-model" type="application/json">{payload}</script>
<script id="agent-live" type="application/json">{live_cfg}</script>
<script>
(function () {{
  var search = document.getElementById('agent-search');
  var filter = document.getElementById('agent-filter');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.agent-card'));
  function apply() {{
    var q = (search.value || '').toLowerCase().trim();
    var mode = filter.value;
    cards.forEach(function (card) {{
      var text = card.getAttribute('data-text') || '';
      var readonly = !!card.querySelector('.agent-mode.quiet');
      card.classList.toggle('is-hidden', (q && text.indexOf(q) < 0) ||
        (mode === 'readonly' && !readonly) || (mode === 'autonomous' && readonly));
    }});
  }}
  search.addEventListener('input', apply); filter.addEventListener('change', apply);
  document.addEventListener('keydown', function (e) {{
    if (e.key === '/' && document.activeElement !== search) {{ e.preventDefault(); search.focus(); }}
  }});
}}());
</script>
</body>
</html>
'''


__all__ = ["render_agent_html"]
