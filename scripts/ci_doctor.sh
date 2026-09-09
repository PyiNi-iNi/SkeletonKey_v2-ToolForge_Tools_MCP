#!/usr/bin/env bash
# CI gate: sk doctor health check
#
# Runs `sk --json doctor` and verifies the install is healthy.
# This is the operator-facing health check: if doctor says ok: false,
# something is broken in the install, state directory, or wire surface.
#
# Exit codes:
#   0  Doctor reports ok: true
#   1  Doctor reports problems
#
# Usage:
#   bash scripts/ci_doctor.sh [--fix] [--no-probe] [--json]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
SK_FLAGS=""
JSON_OUT=false

for arg in "$@"; do
    case "$arg" in
        --fix)      SK_FLAGS="$SK_FLAGS --fix" ;;
        --no-probe) SK_FLAGS="$SK_FLAGS --no-probe" ;;
        --json)     JSON_OUT=true ;;
        --help|-h)
            echo "Usage: $0 [--fix] [--no-probe] [--json]"
            echo ""
            echo "Run sk doctor and verify the install is healthy."
            echo ""
            echo "Options:"
            echo "  --fix       Apply safe repairs (create state dirs, retire stale cache)"
            echo "  --no-probe  Skip the live stdio server test"
            echo "  --json      Output raw JSON"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$REPO_ROOT"

# Find sk
SK_BIN="${REPO_ROOT}/.venv/bin/sk"
if [ ! -x "$SK_BIN" ]; then
    SK_BIN="$(command -v sk 2>/dev/null || true)"
fi
if [ -z "$SK_BIN" ] || [ ! -x "$SK_BIN" ]; then
    echo "sk not found. Install with: pip install -e '.[dev]'" >&2
    exit 1
fi

REPORT=$("$SK_BIN" --json doctor $SK_FLAGS 2>/dev/null)
OK=$(echo "$REPORT" | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin)['ok'])")

if $JSON_OUT; then
    echo "$REPORT"
else
    echo "$REPORT" | "$PYTHON" -c "
import json, sys

d = json.load(sys.stdin)
mark = 'OK' if d['ok'] else 'PROBLEMS'
total = len(d['checks'])
passed = sum(1 for c in d['checks'] if c['ok'])
print(f'  doctor: {mark} ({passed}/{total} checks pass)')
print()
for c in d['checks']:
    status = 'pass' if c['ok'] else 'FAIL'
    print(f'  [{status}] {c[\"id\"]}')
    if not c['ok']:
        for key in ('error', 'hint'):
            val = c.get(key)
            if val:
                print(f'         {key}: {val}')

if d.get('fixes'):
    print()
    print('  Applied fixes:')
    for f in d['fixes']:
        print(f'    {f.get(\"fix\", \"\")} -> {f.get(\"path\", \"\")}')
"
fi

if [ "$OK" = "True" ]; then
    exit 0
else
    exit 1
fi
