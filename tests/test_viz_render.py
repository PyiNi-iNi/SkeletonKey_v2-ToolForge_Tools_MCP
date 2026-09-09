"""V1 of the tool visualizer: the standalone catalog page.

The page is a *view* over the V0 model, so the tests here check the properties
that make it trustworthy and portable rather than how it looks:

* every tool in the model appears on the page, exactly once, advertised or not;
* the page is genuinely standalone - no network reference of any kind, so it
  renders from `file://` on a plane;
* the embedded model is escaped such that no tool's prose can break out of the
  `<script>` block or inject markup (the XSS/DOM-integrity case);
* well-formed markup, checked by an actual parser, not a regex;
* it degrades: the cards are real HTML, present without running any JS.

`html.parser` is stdlib, so this suite obeys ADR-0001 like everything else.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

from skeletonkey.core.manifest import ToolManifest
from skeletonkey.viz import build_viz_model, render_html
from skeletonkey.viz.render import _json_for_script

REPO = Path(__file__).resolve().parents[1]

# Void elements never close; anything else must balance.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class _Structure(HTMLParser):
    """Minimal well-formedness check plus the bits the tests want to count."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.cards: list[dict[str, str]] = []
        self.script_srcs: list[str] = []
        self.link_hrefs: list[str] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            self.ids.append(d["id"])
        if tag == "article" and "card" in (d.get("class") or ""):
            self.cards.append(d)
        if tag == "script" and d.get("src"):
            self.script_srcs.append(d["src"])
        if tag == "link" and d.get("href"):
            self.link_hrefs.append(d["href"])
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"</{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes <{self.stack[-1]}>")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


@pytest.fixture
def model(toolkit):
    return build_viz_model(toolkit.engine.registry)


@pytest.fixture
def page(model):
    return render_html(model)


@pytest.fixture
def parsed(page):
    p = _Structure()
    p.feed(page)
    return p


# ------------------------------------------------------------- completeness

def test_every_tool_appears_as_exactly_one_card(model, parsed):
    ids = [c["data-id"] for c in parsed.cards]
    assert sorted(ids) == sorted(n.id for n in model.nodes)
    assert len(ids) == len(set(ids))


def test_withheld_tools_are_on_the_page_and_marked(model, parsed):
    by_id = {c["data-id"]: c for c in parsed.cards}
    for node in model.nodes:
        card = by_id[node.id]
        assert card["data-adv"] == ("1" if node.advertised else "0")
        if not node.advertised:
            assert "withheld" in card["class"]


def test_card_filter_attributes_match_the_model(model, parsed):
    by_id = {c["data-id"]: c for c in parsed.cards}
    for node in model.nodes:
        card = by_id[node.id]
        assert card["data-group"] == node.group
        assert card["data-risk"] == node.risk
        assert card["data-tier"] == node.tier
        assert card["data-examples"] == str(node.example_count)
        assert card["data-destructive"] == ("1" if node.destructive else "0")
        assert node.id.lower() in card["data-text"]


def test_header_counts_come_from_the_model(model, page):
    agg = model.aggregates
    for value in (agg["registered"], agg["advertised"], agg["withheld"],
                  agg["groups"], agg["capabilities"]):
        assert f">{value}<" in page or f"{value}" in page
    assert model.snapshot["digest"] in page


def test_group_rail_lists_every_group(model, page):
    for g in model.groups:
        assert f'data-group="{g["name"]}"' in page


# ---------------------------------------------------------------- standalone

def test_page_makes_no_network_request(page, parsed):
    assert parsed.script_srcs == [], f"external script: {parsed.script_srcs}"
    assert parsed.link_hrefs == [], f"external stylesheet: {parsed.link_hrefs}"
    for token in ("http://", "https://", "//cdn", "@import", "url("):
        # tool descriptions may legitimately mention a URL; the *page's own*
        # machinery must not. Check the chrome, not the embedded model.
        chrome = page.split('<script id="viz-model"', 1)[0]
        assert token not in chrome, f"page chrome reaches out via {token!r}"


def test_page_is_one_file_with_inline_css_and_js(page):
    assert page.startswith("<!doctype html>")
    assert "<style>" in page and "</style>" in page
    # the model payload, the live config (V2), and the behaviour - all inline
    assert page.count("<script") == 3
    assert page.rstrip().endswith("</html>")


def test_markup_is_well_formed_and_fully_closed(parsed):
    assert parsed.errors == [], parsed.errors
    assert parsed.stack == [], f"unclosed: {parsed.stack}"


def test_the_elements_the_script_reaches_for_all_exist(page, parsed):
    wanted = set(re.findall(r'getElementById\("([^"]+)"\)', page))
    assert wanted, "the behaviour script stopped looking anything up"
    assert wanted <= set(parsed.ids), f"script targets missing ids: {wanted - set(parsed.ids)}"


# ------------------------------------------------------------- embedded data

def test_embedded_model_parses_and_matches(model, page):
    blob = page.split('<script id="viz-model" type="application/json">', 1)[1]
    blob = blob.split("</script>", 1)[0]
    # \u003c and friends are ordinary JSON escapes: a browser's JSON.parse and
    # python's json.loads both read them back as the original characters.
    doc = json.loads(blob)
    assert len(doc["nodes"]) == len(model.nodes)
    assert doc["model_digest"] == model.digest()


def test_a_tool_cannot_break_out_of_the_script_block(toolkit):
    """A description containing `</script>` must not end the payload."""
    reg = toolkit.engine.registry
    nasty = "</script><img src=x onerror=alert(1)> & <b>bold</b>"
    reg.register(
        ToolManifest(id="viz.nasty", title=nasty, description=nasty, risk="none",
                     anti_patterns=[nasty], tags=["</script>"]),
        handler=lambda **kw: None,
    )
    page = render_html(build_viz_model(reg))

    # exactly the three real closing tags survive; the payload's is escaped
    assert page.count("</script>") == 3
    # the payload cannot terminate its block, and nothing became live markup:
    # the hostile string only ever survives entity-escaped as visible text.
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    p = _Structure()
    p.feed(page)
    assert p.errors == [] and p.stack == []
    assert "viz.nasty" in [c["data-id"] for c in p.cards]


def test_json_for_script_neutralises_tag_and_separator_characters():
    out = _json_for_script({"a": "</script>", "b": "\u2028\u2029", "c": "<&>"})
    for ch in ("<", ">", "&", "\u2028", "\u2029"):
        assert ch not in out
    assert json.loads(out)["a"] == "</script>"


def test_no_credential_reaches_the_page(toolkit):
    reg = toolkit.engine.registry
    reg.register(
        ToolManifest(
            id="viz.rendersecret", description="Has a credential arg.", risk="none",
            secret_args=["token"],
            input_schema={"type": "object", "properties": {
                "token": {"type": "string", "default": "ghp_" + "c" * 36}}},
            examples=[{"args": {"token": "xoxb-4242-secret"}}],
        ),
        handler=lambda **kw: None,
    )
    page = render_html(build_viz_model(reg))
    for pattern in (r"ghp_[A-Za-z0-9]{36,}", r"xoxb-[A-Za-z0-9-]+"):
        assert not re.search(pattern, page), f"credential leaked to the page: {pattern}"


# ------------------------------------------------------------ determinism/CLI

def test_render_is_deterministic(model):
    assert render_html(model) == render_html(model)


def test_title_is_used_and_escaped(model):
    page = render_html(model, title='Ops <toolset> & "friends"')
    assert "<title>Ops &lt;toolset&gt; &amp; &quot;friends&quot;</title>" in page
    assert "<toolset>" not in page


def test_embed_model_false_drops_the_payload_but_keeps_the_catalog(model):
    page = render_html(model, embed_model=False)
    assert '<script id="viz-model" type="application/json">null</script>' in page
    p = _Structure()
    p.feed(page)
    assert len(p.cards) == len(model.nodes), "cards must survive without the payload"
    assert p.errors == []


def test_cli_writes_a_page_for_html_and_data_for_json(tmp_path):
    html_out = tmp_path / "tools.html"
    json_out = tmp_path / "model.json"
    for target in (html_out, json_out):
        proc = subprocess.run(
            [sys.executable, "-m", "skeletonkey.cli", "viz", "-o", str(target)],
            capture_output=True, text=True, cwd=str(REPO), timeout=180)
        assert proc.returncode == 0, proc.stderr[-2000:]

    page = html_out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    doc = json.loads(json_out.read_text(encoding="utf-8"))
    p = _Structure()
    p.feed(page)
    assert len(p.cards) == doc["aggregates"]["registered"]


def test_cli_json_flag_still_wins_on_stdout(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "skeletonkey.cli", "viz", "--json"],
        capture_output=True, text=True, cwd=str(REPO), timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout)["schema_version"] == 1
