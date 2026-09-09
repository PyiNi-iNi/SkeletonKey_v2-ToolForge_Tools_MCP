#!/usr/bin/env bash
# CI gate: integration benchmark
#
# Runs a micro-benchmark on each loaded integration's call_tool path.
# This is NOT a network benchmark — it measures the in-process dispatch
# overhead (config lookup, argument validation, handler dispatch). Useful
# for catching regressions in the hot path.
#
# Exit codes:
#   0  Benchmarks completed
#   1  Benchmark failure
#
# Usage:
#   bash scripts/ci_integration_benchmark.sh [--name NAME] [--iterations N] [--json]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
FILTER_NAME=""
ITERATIONS=100
JSON_OUT=false

for arg in "$@"; do
    case "$arg" in
        --name=*)         FILTER_NAME="${arg#--name=}" ;;
        --iterations=*)   ITERATIONS="${arg#--iterations=}" ;;
        --json)           JSON_OUT=true ;;
        --help|-h)
            echo "Usage: $0 [--name NAME] [--iterations N] [--json]"
            echo ""
            echo "Benchmark integration call_tool dispatch overhead."
            echo ""
            echo "Options:"
            echo "  --name=NAME        Benchmark only this integration"
            echo "  --iterations=N     Iterations per integration (default: 100)"
            echo "  --json             Output raw JSON"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$REPO_ROOT"

"$PYTHON" -c "
import json, sys
sys.path.insert(0, '.')
from skeletonkey.integrations import registry
from skeletonkey.integrations.validation import benchmark_integration

name_filter = '${FILTER_NAME}' if '${FILTER_NAME}' else None
iterations = int('${ITERATIONS}')
json_out = True if '${JSON_OUT}' == 'true' else False

loaded = registry.list_loaded_names()
if name_filter:
    names = [name_filter] if name_filter in loaded else []
    if not names:
        print(f'Integration {name_filter!r} is not loaded', file=sys.stderr)
        sys.exit(1)
else:
    names = sorted(loaded)

if not names:
    print('No integrations loaded; nothing to benchmark')
    sys.exit(0)

results = []
for name in names:
    try:
        bench = benchmark_integration(name, iterations=iterations)
        results.append(bench)
    except Exception as exc:
        results.append({'name': name, 'error': str(exc)})

if json_out:
    json.dump({'results': results, 'iterations': iterations}, sys.stdout, indent=2)
    print()
else:
    print(f'=== Integration benchmark ({iterations} iterations each) ===')
    print()
    for r in results:
        name = r.get('name', '?')
        if 'error' in r:
            print(f'  FAIL  {name}: {r[\"error\"]}')
        else:
            tools = r.get('tools_benchmarked', 0)
            mean = r.get('mean_us', 0)
            p99 = r.get('p99_us', 0)
            print(f'  ok    {name:<20}  tools={tools}  mean={mean:.0f}us  p99={p99:.0f}us')
    print()
    print(f'  {len(results)} integration(s) benchmarked')
"
