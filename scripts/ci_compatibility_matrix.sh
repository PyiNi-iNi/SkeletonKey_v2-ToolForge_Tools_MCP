#!/usr/bin/env bash
# CI gate: compatibility matrix report
#
# Generates the SDK/OS compatibility matrix for CI reporting. Each row
# records declared platform support and dependency availability separately,
# so CI can run the same function on each target OS and merge the JSON
# reports without network calls or SDK initialization.
#
# Exit codes:
#   0  Matrix generated successfully
#   1  Generation failed
#
# Usage:
#   bash scripts/ci_compatibility_matrix.sh [--json] [--output FILE] [--name NAME]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
JSON_OUT=false
OUTPUT=""
FILTER_NAME=""

for arg in "$@"; do
    case "$arg" in
        --json)         JSON_OUT=true ;;
        --output=*)     OUTPUT="${arg#--output=}" ;;
        --name=*)       FILTER_NAME="${arg#--name=}" ;;
        --help|-h)
            echo "Usage: $0 [--json] [--output FILE] [--name NAME]"
            echo ""
            echo "Generate the SDK/OS compatibility matrix."
            echo ""
            echo "Options:"
            echo "  --json        Output raw JSON to stdout"
            echo "  --output=FILE Write JSON report to file"
            echo "  --name=NAME   Report only this integration"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$REPO_ROOT"

REPORT=$("$PYTHON" -c "
import json, sys
sys.path.insert(0, '.')
from skeletonkey.integrations.validation import compatibility_matrix

name_filter = '${FILTER_NAME}' if '${FILTER_NAME}' else None
matrix = compatibility_matrix(names=[name_filter] if name_filter else None)
json.dump(matrix, sys.stdout, indent=2)
" 2>/dev/null) || { echo "Failed to generate compatibility matrix" >&2; exit 1; }

if [ -n "$OUTPUT" ]; then
    echo "$REPORT" > "$OUTPUT"
    echo "Matrix written to $OUTPUT" >&2
fi

if $JSON_OUT; then
    echo "$REPORT"
else
    echo "$REPORT" | "$PYTHON" -c "
import json, sys

matrix = json.load(sys.stdin)
ready = [r for r in matrix if r.get('status') == 'ready']
blocked = [r for r in matrix if r.get('status') == 'blocked']
other = [r for r in matrix if r.get('status') not in ('ready', 'blocked')]

print(f'=== Compatibility matrix: {len(matrix)} rows ===')
print(f'  Ready: {len(ready)}  Blocked: {len(blocked)}  Other: {len(other)}')
print()

if blocked:
    print('  Blocked integrations:')
    for r in blocked:
        name = r['name']
        os_name = r.get('os', '?')
        missing = r.get('required_dependencies_missing', [])
        platform_ok = r.get('platform_compatible', True)
        reasons = []
        if not platform_ok:
            reasons.append(f'platform mismatch ({r.get(\"declared_platform\", \"?\")})')
        if missing:
            reasons.append(f'missing deps: {\", \".join(missing)}')
        print(f'    {name:<20} [{os_name}]  {\" | \".join(reasons)}')
    print()

if ready:
    print('  Ready integrations:')
    # Group by name to avoid repeating
    by_name = {}
    for r in ready:
        by_name.setdefault(r['name'], []).append(r.get('os', '?'))
    for name, oses in sorted(by_name.items()):
        print(f'    {name:<20} [{\" | \".join(sorted(oses))}]')
    print()

print(f'  Runner OS: {matrix[0].get(\"runner_os\", \"?\") if matrix else \"?\"}')
"
fi

exit 0
