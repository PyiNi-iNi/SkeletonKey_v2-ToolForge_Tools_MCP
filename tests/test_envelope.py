"""The result envelope is the agent's only stable interface - budget, spill,
artifact cursors, and the error taxonomy that drives recovery."""

from __future__ import annotations

import json

from skeletonkey.core.envelope import DEFAULT_MAX_BYTES, Artifact, ToolResult, _apply_budget
from skeletonkey.core.errors import E, ToolError
from skeletonkey.core.util import clip, estimate_tokens, strip_ansi


def test_success_envelope_shape():
    r = ToolResult.success({"a": 1}, tool="x.y", hints=["do next"])
    d = r.to_dict()
    assert d == {"ok": True, "tool": "x.y", "run_id": d["run_id"], "data": {"a": 1}, "hints": ["do next"]}
    assert "error" not in d and "artifacts" not in d


def test_failure_envelope_carries_code_hint_and_retryability():
    r = ToolResult.failure(E.TIMEOUT, "ran long", tool="shell.run", details={"timeout_s": 5})
    d = r.to_dict()
    assert d["ok"] is False
    err = d["error"]
    assert err["code"] == "TIMEOUT" and err["class"] == "execution"
    assert err["retryable"] is True
    assert "5" in err["hint"], "the timeout value should be interpolated into the hint"
    assert err["details"]["timeout_s"] == 5


def test_missing_placeholder_in_hint_is_visible_not_fatal():
    err = ToolError.from_code(E.MISSING_ARG, "m", details={})
    assert "not reported" in err.hint


def test_error_classes_partition_authority():
    assert E.BAD_ARGS.cls.value == "usage"
    assert E.SANDBOX_VIOLATION.cls.value == "policy"
    assert E.MISSING_BINARY.cls.value == "environment"
    assert E.INTERNAL.cls.value == "internal"
    # policy/environment answers must not look like transient failures
    assert E.SANDBOX_VIOLATION.retryable is False
    assert E.IO.retryable is True


def test_budget_spills_large_data_to_artifact(tmp_path):
    big = "x" * 400_000
    r = ToolResult.success({"content": big}, tool="fs.read")
    d = r.to_dict(max_bytes=8000, spill_dir=str(tmp_path))
    assert len(json.dumps(d).encode("utf-8")) <= 8000, "the advertised budget must be honored"
    assert d["data"]["spilled"] is True and d["data"]["total_bytes"] > 400_000
    assert d["artifacts"][0]["truncated"] is True
    path = d["artifacts"][0]["path"]
    with open(path, encoding="utf-8") as fh:
        restored = json.load(fh)
    assert restored["content"] == big, "the spill file must be the complete payload, not a preview"


def test_budget_without_spill_dir_still_fits(tmp_path):
    r = ToolResult.success("y" * 100_000, tool="t")
    d = r.to_dict(max_bytes=4000, spill_dir=None)
    assert len(json.dumps(d).encode()) <= 4000
    assert "truncated" in " ".join(d["warnings"])


def test_apply_budget_is_a_noop_when_under_limit():
    payload = {"ok": True, "data": {"small": 1}}
    out, spilled = _apply_budget(dict(payload), DEFAULT_MAX_BYTES, spill_dir=None, tool="t")
    assert out == payload and spilled == []


def test_error_only_result_keeps_data_evidence():
    """A failed shell run must still return stdout - that is the diagnostic."""
    r = ToolResult.failure(E.NONZERO_EXIT, "boom", data={"stdout": "partial output", "exit_code": 3})
    d = r.to_dict()
    assert d["data"]["stdout"] == "partial output" and d["error"]["code"] == "NONZERO_EXIT"


def test_metrics_and_estimates_are_populated():
    r = ToolResult.success({"k": "v" * 500})
    r.estimate()
    assert r.metrics.est_tokens > 100
    assert r.metrics.bytes_out == len(r.to_json(max_bytes=None).encode())


def test_artifact_dict_omits_empty_fields():
    a = Artifact(id="art_1", kind="text", bytes=10, path="/tmp/x", truncated=True)
    d = a.to_dict()
    assert d["id"] == "art_1" and d["truncated"] is True
    assert "sha256" not in d and "lines" not in d
    assert d["fetch_rest"]["tool"] == "fs.read"


def test_clip_is_middle_out_and_deterministic():
    text = "".join(f"{i:04}" for i in range(100))
    out = clip(text, 40)
    assert out.startswith("00000") and out.endswith("0099") and "clipped 360 chars" in out
    assert clip("short", 100) == "short"


def test_token_estimate_is_monotonic_and_never_zero_for_content():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("word " * 100) > estimate_tokens("word")


def test_strip_ansi_handles_csi_and_osc():
    assert strip_ansi("\x1b[1;32mok\x1b[0m") == "ok"
    assert strip_ansi("\x1b]8;;http://x\x07link") == "link"


def test_to_text_falls_back_for_hosts_ignoring_structured_content():
    r = ToolResult.success({"n": 1})
    text = r.to_text(max_bytes=500)
    assert json.loads(text)["data"]["n"] == 1


def test_context_and_warnings_survive_serialization():
    r = ToolResult.success(1, context={"cwd": "/tmp"}, warnings=["w"])
    d = r.to_dict()
    assert d["context"]["cwd"] == "/tmp" and d["warnings"] == ["w"]
    r.add_warning("w")
    assert len(r.warnings) == 1, "duplicate warnings must collapse"
