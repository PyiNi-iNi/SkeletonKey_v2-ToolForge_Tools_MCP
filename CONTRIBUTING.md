# Contributing

## Setup

```bash
git clone https://github.com/PyiNi-iNi/SkeletonKey_v2-ToolForge_Tools_MCP
cd SkeletonKey_v2-ToolForge_Tools_MCP

pip install -e ".[mcp,watch,dev]"    # everything, including the MCP server
pytest -q                            # must be green: 1155 passed
```

**Install with `-e`, not just `pip install -r`.** Several integration tests
shell out to the `sk` console script and look it up with `shutil.which("sk")`.
Without an editable install there is no `sk` on PATH and those tests fail with
a confusing `FileNotFoundError` that looks like a code bug.

### The suite must be green

If you see failures on a fresh checkout, it is almost certainly the
environment, not the code. In order of likelihood:

| Symptom | Cause | Fix |
|---|---|---|
| `No module named 'mcp'`, ~40 failures | `[mcp]` extra not installed | `pip install -e ".[mcp]"` |
| `FileNotFoundError: 'sk'` in `test_p7_*` | package not installed editable | `pip install -e .` |
| `Unknown config option: asyncio_mode` | `pytest-asyncio` absent | harmless, or `pip install -e ".[test-mcp]"` |

This repo spent a long stretch with a "known" 27-failure baseline that turned
out to be nothing but an unprovisioned sandbox. If something is red, find out
why before you treat it as normal — and if it is genuinely expected, make it
`skip` with a reason rather than leaving it failing.

### Optional dependencies

The core has **zero** mandatory dependencies (ADR-0001) so it installs inside
locked-down agent sandboxes. That is enforced by CI, not just documented: the
`core-purity` job installs the core alone and fails if anything third-party
was pulled in.

Tests that need an optional extra must **skip**, never fail:

```python
from _extras import HAS_MCP, NO_MCP_REASON

pytestmark = pytest.mark.skipif(not HAS_MCP, reason=NO_MCP_REASON)
```

A skip states honestly that an optional dependency is absent. A failure is
noise, and noise is how a suite stops being believed.

To check your change survives a core-only install:

```bash
pip install -e . pytest && pytest -q -m "not slow"
```

## Before you push

```bash
ruff check .                          # lint, and it is CI-enforced
pytest -q                             # everything
sk viz --summary -o docs/TOOLSET.txt  # if you changed the toolset
```

That last one matters. `docs/TOOLSET.txt` is a committed projection of the
live registry — one line per tool with tier, risk, token cost and exposure. CI
fails when it is stale, which is the point: a new destructive tool, or a tool
that quietly stopped being advertised, shows up as a one-line diff in review
instead of a surprise in production.

**It does not catch everything.** A tool whose *behaviour* changed while its
manifest stayed identical produces no diff. A green snapshot means the shape
of the toolset is unchanged, not that the toolset is safe.

## Architecture decisions

Non-obvious choices live in [`docs/adr/`](docs/adr/) with the reasoning and
the rejected alternatives. If you are about to undo something that looks
strange, read the ADR first — several of them exist specifically because the
obvious simplification is wrong. ADR-0016 through ADR-0018 cover the
visualizer and the dev viewport, including one decision that was deliberately
reversed and what it cost.

New ADRs: copy the shape of an existing one, number it sequentially, and
include the options you considered and the costs you accepted.

## Docs are tested

`tests/test_docs.py` lints every backticked token in `docs/` against the live
registry, the probed capabilities and the config keys. Invent a tool name in a
doc and the suite fails. This is deliberate — documentation that names tools
which do not exist is worse than no documentation.
