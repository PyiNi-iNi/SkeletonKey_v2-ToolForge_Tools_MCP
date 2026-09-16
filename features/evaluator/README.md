# features/evaluator — reward-model evaluation + 1000+ automated suite

Scores any registered model across a deterministic, machine-checkable
suite of 1000+ cases, with a pluggable reward model.

## Architecture

```
suite.py      generate_suite(n=1000, seed) — 8 categories, seeded RNG,
              JSONL-serializable (save_suite/load_suite)
rewards.py    HeuristicReward (zero-dep, 6 dims) | HuggingFaceReward (lazy)
harness.py    Evaluator: aevaluate / astream (live events) / EvalReport
__main__.py   CLI (python -m features.evaluator)
```

## Suite categories

| Category | What it probes | Machine checks |
| --- | --- | --- |
| `json_format` | strict JSON with required keys | json_valid, json_keys |
| `chat_quality` | explanatory writing | min_len, keyword |
| `math` | exact arithmetic | exact answer, max_len |
| `tool_call` | function-calling JSON | json_valid, keys, tool name |
| `length_control` | word budgets | max_words |
| `code_block` | code generation | code fence present |
| `safety_refusal` | refusal behavior | refusal markers |
| `summarization` | one-sentence summaries | keyword, length bounds |

Determinism: same `(n, seed)` → identical suite, so results are
comparable across runs and models. The 1000-case suite is also re-asserted
inside this feature's own pytest file (each case is a test).

## Reward models

- `HeuristicReward` (default): format / length / content / variety /
  coherence / safety, weighted 0.25/0.15/0.30/0.10/0.10/0.10.
- `HuggingFaceReward`: any sequence-classification RM
  (`SK_EVAL_REWARD_MODEL`, e.g. `openai/Llama-2-7b-reward`).
- Failed machine checks hard-cap the score (−0.25 each).

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_EVAL_BACKEND` | `auto` | model backend (via inference_engine) |
| `SK_EVAL_REWARD` | `heuristic` | `heuristic` \| `hf` |
| `SK_EVAL_REWARD_MODEL` | `openai/Llama-2-7b-reward` | HF reward checkpoint |

## Usage

```python
from features.evaluator import Evaluator, generate_suite
from features.inference_engine import InferenceEngine, make_backend

engine = InferenceEngine().register(make_backend())
evaluator = Evaluator(engine, model="mock-1b")

async for event in evaluator.astream(generate_suite(n=1000)):
    if event["stage"] == "case":
        print(event["case_id"], event["score"])
    else:
        print(event["report"]["mean_score"], event["report"]["pass_rate"])
```

```bash
python -m features.evaluator --suite 1000 --model mock-1b --report report.json
python -m features.evaluator --suite-file suite.jsonl --model mistral-7b
```

## Streaming / async

`astream` is an async generator: `start` → per-case `case` events →
`done` (full report). Evaluation runs model calls concurrently-ready
(async), so a UI can live-render the evaluation.

## Dependencies

Core: none. Reward model: `transformers` + `torch` (see
`requirements.txt`). Cross-platform.

## Tests

`tests/` — suite determinism/uniqueness/coverage + JSONL round trip,
heuristic reward per-dimension behavior, check evaluator (all check
types), harness end-to-end (small suite, report shape, category stats,
hard-cap gating), streaming event order, HF reward missing-runtime hint,
CLI smoke test (subprocess) — plus the full **1000-case parameterized
run** (every generated case evaluated as a pytest test).
