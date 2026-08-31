"""Skills: discovery, frontmatter, progressive disclosure, and skill->tool compile."""

from __future__ import annotations

import os

import pytest

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.skills.loader import SkillLoader, _parse_frontmatter

REPO_SKILLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


def write_skill(root, name, *, fm="", body="# instructions\n\nDo the thing.\n", refs=None,
                scripts=None, tool_toml=None):
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    text = "---\n" + fm.strip() + "\n---\n\n" + body
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    if refs:
        (d / "references").mkdir(exist_ok=True)
        for fname, content in refs.items():
            (d / "references" / fname).write_text(content, encoding="utf-8")
    if scripts:
        (d / "scripts").mkdir(exist_ok=True)
        for fname, content in scripts.items():
            (d / "scripts" / fname).write_text(content, encoding="utf-8")
    if tool_toml:
        (d / "tool.toml").write_text(tool_toml, encoding="utf-8")
    return d


# ------------------------------------------------------------------ frontmatter
def test_folded_and_literal_block_scalars_are_supported():
    fm = _parse_frontmatter('name: x\ndescription: >-\n  one\n  two\nwhen_to_use: |\n  a\n  b\ntags: [a, b]\n')
    assert fm["name"] == "x"
    assert fm["description"].strip() == "one two", "folded scalars join with a space"
    assert fm["when_to_use"].startswith("a\nb"), "literal scalars keep line breaks"
    assert fm["tags"] == ["a", "b"]


def test_block_lists_and_nested_maps():
    fm = _parse_frontmatter("requires:\n  - rg\n  - git\nlimits:\n  max: 5\n  strict: true\n")
    assert fm["requires"] == ["rg", "git"]
    assert fm["limits"] == {"max": 5, "strict": True}


def test_quoted_values_and_comments():
    fm = _parse_frontmatter('# a comment\nname: "quoted: colon"\nversion: "1"\npriority: 80\n')
    assert fm["name"] == "quoted: colon"
    assert fm["version"] == "1" and fm["priority"] == 80


def test_no_frontmatter_degrades_with_a_note(tmp_path):
    write_skill(tmp_path, "bare", body="**not frontmatter**\n")
    (tmp_path / "skills" / "bare" / "SKILL.md").write_text("# Title only\n\nbody\n", encoding="utf-8")
    sl = SkillLoader([str(tmp_path / "skills")])
    skill = sl.discover()[0]
    assert skill.description == "Title only"
    assert skill.parse_notes


# ------------------------------------------------------------------ discovery
def test_discovery_requires_a_skill_md(tmp_path):
    write_skill(tmp_path, "real", fm="name: real\ndescription: d\n")
    stray = tmp_path / "skills" / "notes-only"
    stray.mkdir()
    (stray / "README.md").write_text("nothing here", encoding="utf-8")
    sl = SkillLoader([str(tmp_path / "skills")])
    names = [s.name for s in sl.discover()]
    assert names == ["real"]


def test_underscore_prefix_disables_a_skill(tmp_path):
    write_skill(tmp_path, "active", fm="name: active\ndescription: d\n")
    write_skill(tmp_path, "_draft", fm="name: draft\ndescription: d\n")
    sl = SkillLoader([str(tmp_path / "skills")])
    found = {s.name: s for s in sl.discover()}
    assert found["draft"].disabled is True
    assert [s.name for s in sl.match("anything draft")] == [], "disabled skills never inject"


def test_bad_skill_does_not_hide_the_good_ones(tmp_path):
    """One malformed header must not take the whole skill set down with it."""
    write_skill(tmp_path, "good", fm="name: good\ndescription: d\n")
    write_skill(tmp_path, "broken", fm="name: broken\ndescription: d\npriority: [unclosed")
    sl = SkillLoader([str(tmp_path / "skills")])
    assert [s.name for s in sl.discover()] == ["good"]
    assert sl.errors and "broken" in sl.errors[0]["path"]
    assert "ValueError" in sl.errors[0]["error"] or "int" in sl.errors[0]["error"]


def test_duplicate_name_is_reported_not_silently_replaced(tmp_path):
    write_skill(tmp_path, "a", fm="name: dup\ndescription: first\n")
    write_skill(tmp_path, "b", fm="name: dup\ndescription: second\n")
    sl = SkillLoader([str(tmp_path / "skills")])
    found = sl.discover()
    assert len(found) == 1
    assert "shadowed" in found[0].parse_notes[0]


def test_missing_dir_is_not_an_error(tmp_path):
    sl = SkillLoader([str(tmp_path / "nope")])
    assert sl.discover() == [] and sl.errors == []


def test_unknown_skill_names_near_misses():
    sl = SkillLoader([REPO_SKILLS])
    with pytest.raises(SkeletonKeyError) as exc:
        sl.get("shell-cross")
    assert exc.value.code == E.ENOENT.code
    assert exc.value.details["did_you_mean"] == ["shell-crossplatform"]
    assert "skills.list" in exc.value.details["advice"]


# ------------------------------------------------------------------ injection
def test_injection_is_bounded_and_says_it_was_truncated(tmp_path):
    long_body = "\n\n".join(f"## section {i}\n\n" + ("word " * 60) for i in range(30))
    write_skill(tmp_path, "big", fm="name: big\ndescription: d\n", body=long_body)
    sl = SkillLoader([str(tmp_path / "skills")])
    skill = sl.discover()[0]
    text = skill.render_injection(max_tokens=200)
    assert len(text) < 200 * 4 + 200
    assert "truncated" in text
    assert text.startswith("# skill: big")
    assert text.endswith("\n")


def test_references_are_read_only_on_demand(tmp_path):
    write_skill(tmp_path, "withrefs", fm="name: withrefs\ndescription: d\n",
                refs={"detail.md": "IMPORTANT DETAIL TEXT"})
    sl = SkillLoader([str(tmp_path / "skills")])
    skill = sl.discover()[0]
    assert skill.references == ["references/detail.md"]
    assert "IMPORTANT" not in skill.render_injection(max_tokens=4000)
    with_ref = skill.render_injection(max_tokens=4000, with_references=["references/detail.md"])
    assert "IMPORTANT DETAIL TEXT" in with_ref
    assert "## reference: references/detail.md" in with_ref
    missing = skill.render_injection(max_tokens=4000, with_references=["references/nope.md"])
    assert "[missing]" in missing, "a bad reference must be visible, not silent"


def test_context_block_reports_which_skills_matched_and_why_budget():
    sl = SkillLoader([REPO_SKILLS])
    cb = sl.context_block("run this command on windows in powershell")
    assert [s["name"] for s in cb["skills"]] == ["shell-crossplatform"]
    assert 0 < cb["tokens"] <= cb["budget"]
    assert cb["unused_budget"] == cb["budget"] - cb["tokens"]
    assert "quoting" in cb["block"].lower() or "dialect" in cb["block"].lower()


def test_context_block_is_empty_when_nothing_matches():
    sl = SkillLoader([REPO_SKILLS])
    cb = sl.context_block("how do I bake a souffl\u00e9")
    # The keys are identical whether or not something matched: a caller that has to
    # guard `cb["unused_budget"]` for the empty case is a caller that crashes there.
    assert cb["block"] == "" and cb["skills"] == [] and cb["tokens"] == 0
    assert cb["budget"] == sl.max_inline_tokens * 3  # limit defaults to 3
    assert cb["unused_budget"] == cb["budget"]


def test_max_inline_tokens_caps_the_injected_body(tmp_path):
    """`skills.max_inline_tokens` is a real knob, not decoration (ADR-0005)."""
    write_skill(tmp_path, "long", fm="name: long\ndescription: rename symbol across repo\n",
                body="paragraph text here " * 300)  # ~5.7k chars, ~1.4k tokens
    tight = SkillLoader([str(tmp_path / "skills")], max_inline_tokens=200)
    cb = tight.context_block("rename symbol across repo")
    assert "[truncated" in cb["block"], "the budget must actually cut the body"
    assert len(cb["block"]) <= 200 * 4 + 80, "chars = 4x tokens, plus the header"
    assert cb["unused_budget"] >= 0
    wide = SkillLoader([str(tmp_path / "skills")], max_inline_tokens=4000)
    assert "[truncated" not in wide.context_block("rename symbol across repo")["block"]


def test_respect_priority_turns_the_tiebreak_off(tmp_path):
    write_skill(tmp_path, "aaa", fm="name: aaa\ndescription: zeta overlap tokens here\npriority: 10\n",
                body="a")
    write_skill(tmp_path, "bbb", fm="name: bbb\ndescription: zeta overlap tokens here\npriority: 90\n",
                body="b")
    task = "zeta overlap tokens here"
    assert SkillLoader([str(tmp_path / "skills")]).match(task)[0].name == "bbb"
    loose = SkillLoader([str(tmp_path / "skills")], respect_priority=False)
    assert loose.match(task)[0].name == "aaa", "equal scores fall back to the name order"


def test_match_respects_the_token_budget_over_several_skills(tmp_path):
    for i in range(4):
        write_skill(tmp_path, f"s{i}", fm=f"name: s{i}\ndescription: alpha beta gamma\n",
                    body="word " * 500)
    sl = SkillLoader([str(tmp_path / "skills")])
    picked = sl.match("alpha beta gamma", limit=9, max_tokens=1500)
    assert 0 < len(picked) < 4, "the budget must stop before blowing the prompt"


# ------------------------------------------------------------------ -> tools
def test_tool_toml_declares_real_manifests(tmp_path):
    write_skill(tmp_path, "decl", fm="name: decl\ndescription: d\n", tool_toml=(
        '[[tool]]\n'
        'id = "decl.thing"\n'
        'title = "Thing"\n'
        'description = "does the declared thing"\n'
        'risk = "read"\n'
        'capability = "decl.thing"\n'
        'input_schema = """\n{"type":"object","properties":{"x":{"type":"string"}},"required":["x"]}\n"""\n'
    ))
    sl = SkillLoader([str(tmp_path / "skills")])
    cands = sl.manifest_candidates()
    assert [c["id"] for c in cands] == ["decl.thing"]
    assert cands[0]["source"] == "skill:decl"
    assert cands[0]["group"] == "skill.decl"

    from skeletonkey.core.manifest import ToolManifest

    man = ToolManifest.from_dict(cands[0])
    assert man.input_schema["required"] == ["x"], "a schema written as a TOML string must parse"
    assert man.source == "skill:decl"


def test_single_table_and_broken_toml_forms(tmp_path):
    write_skill(tmp_path, "one", fm="name: one\ndescription: d\n",
                tool_toml='[tool]\nid = "one.x"\ndescription = "d"\n')
    write_skill(tmp_path, "two", fm="name: two\ndescription: d\n", tool_toml="[[tool]\nbroken")
    sl = SkillLoader([str(tmp_path / "skills")])
    sl.discover()
    assert [c["id"] for c in sl.manifest_candidates()] == ["one.x"]
    notes = {s.name: s.parse_notes for s in sl.discover()}
    assert any("unreadable" in n for n in notes["two"]), "the broken file must say so"


def test_repo_ships_the_two_documented_skills():
    sl = SkillLoader([REPO_SKILLS])
    found = {s.name: s for s in sl.discover()}
    assert set(found) >= {"shell-crossplatform", "fs-safe-refactor"}
    for name, skill in found.items():
        assert skill.description, name
        assert skill.when_to_use, name
        assert skill.token_estimate < 4000, f"{name} is too big to inject; move detail to references/"
        assert skill.allowed_tools, f"{name} should name the tools it expects"
        assert skill.body.startswith("# "), f"{name} should open with a heading"


def test_repo_skill_bodies_do_not_promise_tools_that_do_not_exist():
    """Skill guidance is load-bearing: every `fs.*`/`shell.*` it names must be real."""
    from skeletonkey.toolkit import build

    tk = build()
    known = {m.id for m in tk.engine.registry.all()}
    import re

    sl = SkillLoader([REPO_SKILLS])
    for skill in sl.discover():
        for ref in skill.references:
            path = os.path.join(skill.path, ref)
            with open(path, encoding="utf-8") as fh:
                skill.body += "\n" + fh.read()
        # A filename whose prefix is also a tool group (`profile.json` in the state
        # layout) is not a tool id; everything else that looks like `group.name` must
        # be registered, because a skill body that names a missing tool is a lie.
        file_ext = (".json", ".ndjson", ".toml", ".md", ".txt", ".py", ".tar", ".lock")
        tokens = {t for t in re.findall(r"\b(?:fs|shell|registry|profile|skills)\.[a-z_]+", skill.body)
                  if not t.endswith(file_ext)}
        for token in tokens:
            assert token in known, f"{skill.name} tells the agent to call {token}, which is not registered"
