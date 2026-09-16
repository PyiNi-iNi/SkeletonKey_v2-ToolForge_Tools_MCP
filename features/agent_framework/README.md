# features/agent_framework — AutoGen agents, code execution, CSV, web search

Agent primitives on top of `inference_engine` + platform infra.

## Architecture

```
autogen.py      Agent + AutoGenConversation (round-robin group chat,
                termination token/predicate) — the AutoGen pattern, local
exec_agent.py   PythonExecAgent (isolated subprocess: -I, minimal env,
                timeout kill) + CodeAgent (generate → execute → stderr
                feedback loop)
csv_agent.py    CSVAgent: describe / query (and/or expression language) /
                aggregate (group_by + sum/mean/count/min/max)
websearch.py    DuckDuckGoSearch (keyless HTML) | MockSearch + SearchAgent
                (search → ground → cited answer)
```

## Sandbox semantics

`PythonExecAgent` runs code with:

- `sys.executable -I` (isolated: no user site-packages, no `PYTHONPATH`)
- minimal allowlisted env (PATH/HOME/TMPDIR/USER/LANG/PYTHONHASHSEED/TZ)
- temp cwd (or a caller-provided workdir)
- wall-clock timeout → SIGKILL, reported as `timed_out`

This is a **defense for accidental bad code, not a security boundary** —
hostile code needs a container (see `skeletonkey/fsx`).

## Usage

```python
from features.agent_framework import (
    Agent, AutoGenConversation, CodeAgent, CSVAgent, SearchAgent,
    PythonExecAgent, MockSearch,
)
from features.inference_engine import InferenceEngine

engine = InferenceEngine()

# 1. code agent: generate → run → fix (3 rounds max)
report = await CodeAgent(engine, PythonExecAgent(timeout=10)).solve(
    "Print the sum of the first 10 even numbers")
print(report.success, report.report.stdout)

# 2. CSV agent
csv = CSVAgent(path="sales.csv").load()
print(csv.describe())
print(csv.query("region == 'EMEA' and amount > 100"))
print(csv.aggregate("region", {"amount": "sum", "amount": "mean"}))

# 3. web-search grounded answer
agent = SearchAgent(engine, MockSearch())
result = await agent.ask("How do attention sinks work?")
print(result["answer"], result["sources"])

# 4. multi-agent conversation
talk = AutoGenConversation([
    Agent("coder", engine, model="mock-1b",
          system="You write code. Say APPROVE when done."),
    Agent("reviewer", engine, model="mock-1b",
          system="You review. Say APPROVE when the code is fine."),
], max_rounds=6)
conv = await talk.run("Design a rate limiter")
print(conv.rounds, conv.terminated)
```

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_AGENT_EXEC_TIMEOUT` | `10` | code execution timeout (s) |
| `SK_AGENT_MAX_ROUNDS` | `3` | CodeAgent generate/execute rounds |
| `SK_AGENT_SEARCH_ENDPOINT` | `https://duckduckgo.com/html/` | DDG HTML endpoint (proxy-able) |

## Dependencies

Core: none (pandas / autogen / ddgs optional, see `requirements.txt`).

## Tests

`tests/` — exec sandbox (stdout/stderr/exit codes, timeout kill, `-I`
isolation, minimal env), CodeAgent retry loop with scripted failures,
CSV describe/query/aggregate (incl. OR semantics + bad expr errors),
DDG HTML parser fixture, mock search determinism, SearchAgent citation
flow, AutoGen termination token + max rounds + ordering.
