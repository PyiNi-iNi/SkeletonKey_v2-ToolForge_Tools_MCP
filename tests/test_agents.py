"""ADR-0019: purpose-driven agent directory and its read-only workspace."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

from skeletonkey.agents import (
    AGENT_PROFILES,
    build_agent_model,
    create_agent,
    render_agent_html,
)
from skeletonkey.viz.server import VizServer


class _AgentPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards: list[str] = []
        self.tools: list[str] = []
        self.script_srcs: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attrs = dict(attrs)
        if tag == "article" and "agent-card" in (attrs.get("class") or ""):
            self.cards.append(attrs.get("data-agent", ""))
        if tag == "li" and "tool-row" in (attrs.get("class") or ""):
            self.tools.append(attrs.get("data-tool", ""))
        if tag == "script" and attrs.get("src"):
            self.script_srcs.append(attrs["src"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])


def test_every_live_tool_has_a_purpose_assignment(toolkit):
    model = build_agent_model(toolkit.engine.registry)
    assert len(model.agents) == len(AGENT_PROFILES)
    assert model.tool_count == len(toolkit.engine.registry.all())
    assert model.assigned_once
    assert model.unassigned_tools == []
    assigned = {tool.id for agent in model.agents for tool in agent.tools}
    assert assigned == {manifest.id for manifest in toolkit.engine.registry.all()}


def test_agent_projection_is_deterministic_and_carries_registry_digest(toolkit):
    first = build_agent_model(toolkit.engine.registry)
    second = build_agent_model(toolkit.engine.registry)
    assert first.to_json() == second.to_json()
    assert first.digest() == second.digest()
    assert first.tool_digest
    assert first.to_json()["schema_version"] == 1


def test_purpose_profiles_have_specific_actions_and_limits():
    assert len({p.id for p in AGENT_PROFILES}) == len(AGENT_PROFILES)
    for profile in AGENT_PROFILES:
        assert profile.purpose and profile.mission
        assert profile.actions
        assert profile.max_calls > 0
        assert profile.max_wall_time_s > 0
        assert profile.max_output_bytes > 0
        assert all(action.capabilities for action in profile.actions)


def test_read_only_purpose_marks_write_tools_gated(toolkit):
    model = build_agent_model(toolkit.engine.registry)
    analyst = next(agent for agent in model.agents if agent.id == "workspace-analyst")
    assert analyst.read_only is True
    assert any(tool.status == "read-only" for tool in analyst.tools)
    assert all(tool.status != "ready" for tool in analyst.tools if tool.risk in {
        "write", "destructive", "privileged"
    })


def test_runtime_agent_uses_the_existing_subagent_boundary(toolkit):
    agent = create_agent(toolkit.engine, "workspace-analyst")
    assert agent.spec.name == "Workspace Analyst"
    assert agent.spec.read_only is True
    assert agent.spec.tier == "task"
    assert "fs.read" in (agent.spec.allowed_tools or [])
    assert "fs.write" in (agent.spec.allowed_tools or [])
    assert "purpose-agent" in agent.spec.tags


def test_agent_page_is_standalone_and_contains_all_tools(toolkit):
    model = build_agent_model(toolkit.engine.registry)
    page = render_agent_html(model, catalog_url="/")
    parsed = _AgentPage()
    parsed.feed(page)
    assert page.startswith("<!doctype html>")
    assert len(parsed.cards) == len(model.agents)
    assert set(parsed.tools) >= {manifest.id for manifest in toolkit.engine.registry.all()}
    assert parsed.script_srcs == []
    assert "https://" not in page.split('<script id="agent-model"', 1)[0]
    assert 'href="/"' in page
    assert "ADR-0019 contract" in page
    assert page.count('type="application/json"') == 2


def test_agent_page_embedded_json_is_escaped_and_valid(toolkit):
    page = render_agent_html(build_agent_model(toolkit.engine.registry))
    blob = page.split('<script id="agent-model" type="application/json">', 1)[1]
    blob = blob.split("</script>", 1)[0]
    document = json.loads(blob)
    assert document["assigned_tool_count"] == document["tool_count"]
    assert document["unassigned_tools"] == []
    assert "</script>" not in blob


def test_agent_server_exposes_json_and_html_without_a_write_surface(toolkit):
    server = VizServer(toolkit.engine.registry, port=0)
    server.start()

    def get(path: str):
        try:
            with urllib.request.urlopen(server.url + path, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    try:
        status, body = get("/agents")
        assert status == 200
        assert b"Agent workspace" in body
        status, body = get("/api/agents")
        assert status == 200
        document = json.loads(body)
        expected = build_agent_model(toolkit.engine.registry)
        assert document["tool_count"] == expected.tool_count
        assert document["assigned_tool_count"] == expected.assigned_tool_count
        status, body = get("/wat")
        assert status == 404
        assert b"/api/agents" in body
    finally:
        server.stop()

    # The server is intentionally a GET-only projection.  The handler source
    # has no do_POST/do_PUT/do_DELETE/do_PATCH methods.
    with open("skeletonkey/viz/server.py", encoding="utf-8") as fh:
        source = fh.read()
    assert not re.search(r"^\s+def do_(POST|PUT|DELETE|PATCH)\(", source, re.MULTILINE)
