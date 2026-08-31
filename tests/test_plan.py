"""P4: toolkit.plan() - the autopilot loop's integration surface."""

from __future__ import annotations

import json


def test_plan_returns_shortlist_skills_budgets_and_replay(toolkit):
    task = "rename a symbol across the codebase"
    p = toolkit.plan(task)
    assert p["task"] == task

    assert p["shortlist"], "a real task must get a shortlist"
    scores = [r["score"] for r in p["shortlist"]]
    assert scores == sorted(scores, reverse=True), "ranked means ranked"
    for row in p["shortlist"]:
        for key in ("id", "title", "score", "tokens_estimate", "typical_output_bytes", "replay"):
            assert key in row, row
        assert row["replay"]["tool"] == row["id"]
        assert toolkit.registry.has(row["id"])
        # the replay is runnable: the sk_call's json args parse back to `args`
        call = row["replay"]["sk_call"]
        assert call.startswith(f"sk call {row['id']} '") and call.endswith("'")
        inner = call[len(f"sk call {row['id']} '"):-1]
        assert json.loads(inner) == row["replay"]["args"]

    # the skills block keeps its stable shape (the empty case included)
    assert set(p["skills"]) >= {"block", "skills", "tokens", "budget", "strategy"}

    # the exact budgets the loop must charge against - straight from config
    cfg = toolkit.config
    assert p["budgets"]["task_max_calls"] == cfg.budget.task_max_calls
    assert p["budgets"]["task_max_tokens_out"] == cfg.budget.task_max_tokens_out
    assert p["budgets"]["max_output_bytes"] == cfg.budget.max_output_bytes


def test_plan_shortlist_respects_k_and_empty_tasks(toolkit):
    p = toolkit.plan("find every occurrence of a string in files", k=2)
    assert len(p["shortlist"]) <= 2
    # no query tokens: an honest empty shortlist, not a crash
    assert toolkit.plan("")["shortlist"] == []


def test_plan_names_a_skill_that_matches_the_task(toolkit, workspace):
    (skill_dir := workspace / "skills" / "rename-helper").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: rename-helper\ndescription: Rename a symbol safely and reversibly\n"
        "when_to_use: [rename a symbol, rename, reversible]\n"
        "tags: [rename, refactor]\n---\n\nBody.\n",
        encoding="utf-8")
    toolkit.skills.discover(refresh=True)
    p = toolkit.plan("safely rename a symbol and keep the change reversible", k=8)
    names = [s.get("name") for s in p["skills"]["skills"]]
    assert "rename-helper" in names, "the task that describes this skill must match it"
