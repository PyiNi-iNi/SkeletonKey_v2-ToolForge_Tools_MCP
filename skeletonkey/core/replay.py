"""Replay and eval harness (P4).

A **run recording** is a JSONL file:

    line 1: {"meta": {"task_id": …, "workspace": …, "state_dir": …, "recorded_at": …}}
    line n: {"seq": i, "tool": "fs.patch", "args": {…}, "result": <full envelope>}

`RunRecorder` is the loop's write side (it holds the full envelopes, so the
recording stores them in full - the ledger's 400-byte previews are for audit,
not replay). `replay()` re-executes the steps against a fresh *copy* of the
recorded workspace (the original is never touched) and diffs the envelopes.

Normalization is **explicit, not fuzzy**: volatile keys are dropped wherever
they occur, the recorded workspace/state roots are rewritten to placeholders,
and tools that declared themselves `stateful` (session or host state) are
compared on `ok` + error code only - a stateful result is allowed to differ in
data, which is what the declaration promised.

`eval_suite()` scores scripted tasks: success, calls per task, tokens per
task, and the refusal-then-recovery rate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import tempfile
import time
from typing import Any

from .config import Config
from .util import compact_json

# dropped anywhere in the tree: identity, wall time (incl. file mtimes - a
# replayed write happens at a different instant), and wire-payload size
VOLATILE_KEYS = frozenset({
    "run_id", "trace_id", "ts", "started", "elapsed_s", "wall_s",
    "duration_ms", "est_tokens", "bytes_out", "mtime", "atime",
})

# journal tokens are per-call identities (und_<12hex>), like run_id: rewrite the
# value wherever it appears (top-level undo_token, undo.args.token, journal rows)
JOURNAL_TOKEN_RE = re.compile(r"^und_[0-9a-f]{12}$")

# the harness replays/scores runs unattended: every risk class auto-approved,
# with confirm_destructive off - exactly the pairing the engine's own refusal
# advice names for unattended runs
_UNATTENDED_POLICY = {"auto_approve": ["none", "read", "write", "destructive", "network",
                                       "privileged"], "confirm_destructive": False}


# --------------------------------------------------------------------- recorder
class RunRecorder:
    """Append full envelopes to a run recording, one step per line.

    The run's *starting* state is snapshotted to `<recording>.baseline` before
    the first step: a mutation run changes the tree, and a replay that starts
    from the run's END state would be re-running the steps against their own
    results. (The state dir is included in the snapshot as found at start -
    empty for a fresh run - and the replay keeps its own fresh state dir.)
    """

    def __init__(self, path: str | os.PathLike, *, meta: dict[str, Any] | None = None) -> None:
        self.path = str(path)
        self._seq = 0
        meta = dict(meta or {})
        ws = meta.get("workspace", "")
        if ws and os.path.isdir(ws):
            baseline = self.path + ".baseline"
            if os.path.isdir(baseline):
                shutil.rmtree(baseline)
            # copy2: mtime preserved, so time-echoing data stays comparable
            shutil.copytree(ws, baseline)
            meta["baseline"] = baseline
        self._fh = open(self.path, "w", encoding="utf-8", newline="\n")  # noqa: SIM115 - owned by recorder
        self._fh.write(compact_json({"meta": {
            "task_id": meta.get("task_id", ""),
            "workspace": meta.get("workspace", ""),
            "baseline": meta.get("baseline", ""),
            "state_dir": meta.get("state_dir", ""),
            "recorded_at": meta.get("recorded_at", time.time()),
        }}) + "\n")

    def record(self, tool: str, args: dict[str, Any], result: Any) -> None:
        env = result.to_dict(max_bytes=None) if hasattr(result, "to_dict") else result
        self._seq += 1
        self._fh.write(compact_json({"seq": self._seq, "tool": tool,
                                     "args": args, "result": env}) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except (OSError, ValueError):
                pass
            self._fh = None

    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def load_recording(path: str | os.PathLike) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if not line.strip():
                continue
            row = json.loads(line)
            if i == 0 and "meta" in row:
                meta = row["meta"]
                continue
            steps.append(row)
    if not meta:
        raise ValueError(f"{path}: recording is missing its meta line")
    return meta, steps


# --------------------------------------------------------------- normalization
def _rewrite(text: str, roots: list[tuple[str, str]]) -> str:
    # longest root first: a state dir nested under the workspace must win over it
    for root, placeholder in sorted(roots, key=lambda r: -len(r[0] or "")):
        if root and text.startswith(root) and (len(text) == len(root)
                                               or text[len(root)] in "/\\"):
            return placeholder + text[len(root):].replace(os.sep, "/")
    if JOURNAL_TOKEN_RE.match(text):
        return "<JOURNAL_TOKEN>"
    return text


def normalize(obj: Any, roots: list[tuple[str, str]]) -> Any:
    """Explicit normalization for replay comparison:

    - drop volatile keys (identity, wall time, wire size) wherever they occur;
    - rewrite the workspace/state roots to `<WS>`/`<STATE>` so a replay on a
      scratch copy is comparable to the recording.

    Nothing else is touched - a diff is a real difference.
    """
    if isinstance(obj, dict):
        return {k: normalize(v, roots) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [normalize(v, roots) for v in obj]
    if isinstance(obj, str):
        return _rewrite(obj, roots)
    return obj


def _diff(a: Any, b: Any, path: str = "$") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: missing in recording")
            elif k not in b:
                out.append(f"{path}.{k}: missing in replay")
            else:
                out.extend(_diff(a[k], b[k], f"{path}.{k}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        if len(a) != len(b):
            out.append(f"{path}: list length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            out.extend(_diff(x, y, f"{path}[{i}]"))
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return [] if a == b else [f"{path}: {a!r} != {b!r}"]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


# ----------------------------------------------------------------------- replay
def replay(recording: str | os.PathLike, *, auto_approve: bool = True) -> dict[str, Any]:
    """Re-execute a recording in a scratch copy and diff the envelopes."""
    from ..toolkit import build
    from .engine import CallContext

    meta, steps = load_recording(recording)
    # the baseline (start-of-run snapshot) is authoritative when present: the
    # run's end state in the live workspace already contains its own results
    src_ws = meta.get("baseline") or meta.get("workspace", "")
    if not os.path.isdir(src_ws):
        return {"ok": False, "error": f"recorded workspace {src_ws!r} no longer exists",
                "steps": []}
    scratch = tempfile.mkdtemp(prefix="sk-replay-")
    ws = os.path.join(scratch, "ws")
    state = os.path.join(scratch, "state")
    # copy2 preserves mtime: fs.stat-style data survives the move intact
    shutil.copytree(src_ws, ws)
    overrides: dict[str, Any] = {"roots": [ws], "state": {"dir": state}, "log_level": "ERROR"}
    if auto_approve:
        # unattended: every risk class, exactly as the SKELETONKEY_AUTO_APPROVE mode
        overrides["policy"] = dict(_UNATTENDED_POLICY)
    cfg = Config.load(cwd=scratch, overrides=overrides)
    tk = build(config=cfg)
    try:
        # the SAME task id as the recording: journal data echoes it, and a
        # fresh state dir makes reuse safe
        ctx = CallContext.from_config(cfg, task_id=meta.get("task_id") or "replay")
        # rewrite roots are the LIVE workspace (where the envelopes were made),
        # even though the copy came from the baseline snapshot
        live_ws = meta.get("workspace") or src_ws
        rec_roots = [(live_ws, "<WS>"),
                     (meta.get("state_dir") or os.path.join(live_ws, ".sk"), "<STATE>")]
        rep_roots = [(ws, "<WS>"), (state, "<STATE>")]
        rows: list[dict[str, Any]] = []
        tokens = 0
        for step in steps:
            tool_id = step["tool"]
            man = tk.registry.get(tool_id) if tk.registry.has(tool_id) else None
            stateful = bool(man is not None and man.stateful != "none")
            res = tk.engine.call(tool_id, step.get("args") or {}, ctx=ctx)
            got = res.to_dict(max_bytes=None)
            rec = step.get("result") or {}
            rec_err = (rec.get("error") or {}).get("code")
            got_err = (got.get("error") or {}).get("code")
            ok_agree = bool(rec.get("ok", True)) is res.ok
            if not ok_agree:
                match, diffs = False, ["ok flag differs"]
            elif rec_err != got_err:
                match, diffs = False, [f"error code: {rec_err!r} != {got_err!r}"]
            elif stateful:
                # a stateful tool promises its data may reflect live state:
                # ok + error code are the contract, and that is all we hold it to
                match, diffs = True, []
            else:
                diffs = _diff(normalize(rec.get("data"), rec_roots),
                              normalize(got.get("data"), rep_roots), "data")
                match = not diffs
            tokens += got.get("metrics", {}).get("est_tokens", 0)
            rows.append({"seq": step.get("seq"), "tool": tool_id, "match": match,
                         "stateful": stateful, "diffs": diffs[:10]})
        ledger_path = os.path.join(state, "ledger.ndjson")
        ledger_rows = 0
        if os.path.exists(ledger_path):
            with open(ledger_path, encoding="utf-8") as fh:
                ledger_rows = sum(1 for ln in fh if ln.strip())
        return {"ok": all(r["match"] for r in rows), "steps": rows,
                "calls": len(rows), "tokens": tokens,
                "ledger_rows": ledger_rows,
                "ledger_one_row_per_call": ledger_rows == len(rows),
                "scratch": scratch}
    finally:
        tk.close()


# -------------------------------------------------------------------------- eval
def _partial_match(expected: Any, actual: Any) -> bool:
    """Assert a subset: every key in `expected` must hold in `actual`."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(k in actual and _partial_match(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return expected == actual
    return expected == actual


_REF_RE = re.compile(r"^\$(\d+)\.data\.([A-Za-z0-9_.\-]+)$")


class _RefError(ValueError):
    pass


def _resolve_args(args: Any, history: list[dict[str, Any]]) -> Any:
    """Resolve `$<step>.data.<path>` references to earlier steps' data.

    A scripted task can only know a job_id (or an undo token) after the step
    that produced it; the reference is how a static script stays honest about
    that. A missing reference fails the task explicitly.
    """
    def walk(v: Any) -> Any:
        if isinstance(v, str):
            m = _REF_RE.match(v)
            if m:
                step_no, path = int(m.group(1)), m.group(2)
                env = history[step_no - 1] if 1 <= step_no <= len(history) else None
                cur: Any = (env or {}).get("data")
                for part in path.split("."):
                    if not isinstance(cur, dict) or part not in cur:
                        raise _RefError(f"${step_no}.data.{path} is not in step {step_no}'s data")
                    cur = cur[part]
                return cur
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(args)


def eval_suite(suite_paths: list[str | os.PathLike]) -> dict[str, Any]:
    """Score a suite of scripted tasks (one JSON object per line).

    Task shape: {id, task, setup: {relpath: content}, steps: [{tool, args}],
    expect: {ok?, data?, no_warnings?}}. `expect` is asserted against the final
    envelope; `no_warnings` applies to every step. A step refusal that the task
    recovers from (final ok) counts as refusal-then-recovery.

    A step's args may reference an earlier step's data as
    `"$<step>.data.<path>"` (e.g. `"$1.data.job_id"`) - the only way a static
    script can use values it can only know after the fact.
    """
    from ..toolkit import build
    from .engine import CallContext

    tasks: list[dict[str, Any]] = []
    for path in suite_paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    tasks.append(json.loads(line))

    rows: list[dict[str, Any]] = []
    for task in tasks:
        scratch = tempfile.mkdtemp(prefix="sk-eval-")
        ws = os.path.join(scratch, "ws")
        state = os.path.join(scratch, "state")
        os.makedirs(ws, exist_ok=True)
        for rel, content in (task.get("setup") or {}).items():
            p = os.path.join(ws, rel)
            os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        cfg = Config.load(cwd=scratch, overrides={
            "roots": [ws], "state": {"dir": state},
            "policy": dict(_UNATTENDED_POLICY), "log_level": "ERROR",
        })
        tk = build(config=cfg)
        try:
            ctx = CallContext.from_config(cfg, task_id=task["id"])
            tokens = 0
            warnings: list[str] = []
            refused: list[str] = []
            final: dict[str, Any] | None = None
            history: list[dict[str, Any]] = []
            ref_fail: str | None = None
            for step in task["steps"]:
                try:
                    args = _resolve_args(step.get("args") or {}, history)
                except _RefError as exc:
                    ref_fail = str(exc)
                    break
                res = tk.engine.call(step["tool"], args, ctx=ctx)
                env = res.to_dict(max_bytes=None)
                final = env
                history.append(env)
                tokens += env.get("metrics", {}).get("est_tokens", 0)
                warnings.extend(env.get("warnings") or [])
                if not res.ok:
                    refused.append(f"{step['tool']}:{(env.get('error') or {}).get('code')}")
        finally:
            tk.close()

        expect = task.get("expect") or {}
        final_ok = bool(final is not None and final.get("ok"))
        ok_pass = final_ok if expect.get("ok", True) else not final_ok
        data_pass = _partial_match(expect.get("data", {}), final.get("data") if final else None) \
            if "data" in expect else True
        warn_pass = True if expect.get("no_warnings") is not True else not warnings
        ref_pass = ref_fail is None
        passed = ref_pass and ok_pass and data_pass and warn_pass
        rows.append({
            "id": task["id"], "ok": passed,
            "calls": len(task["steps"]), "tokens": tokens,
            "warnings": warnings[:3], "refused": refused,
            "recovered": bool(refused and final_ok),
            "fail": ([] if ref_pass else [f"ref: {ref_fail}"])
                   + ([] if ok_pass else ["ok"]) + ([] if data_pass else ["data"])
                   + ([] if warn_pass else ["warnings"]),
            "scratch": scratch,
        })

    calls = [r["calls"] for r in rows]
    return {
        "tasks": len(rows),
        "passed": sum(1 for r in rows if r["ok"]),
        "median_calls_per_task": statistics.median(calls) if calls else 0,
        "mean_tokens_per_task": round(sum(r["tokens"] for r in rows) / len(rows), 1) if rows else 0,
        "refusals": sum(len(r["refused"]) for r in rows),
        "refusal_then_recovery": sum(1 for r in rows if r["recovered"]),
        "results": rows,
    }
