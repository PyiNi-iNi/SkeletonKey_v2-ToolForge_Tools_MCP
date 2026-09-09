#!/usr/bin/env bash
# CI gate: dependency audit
#
# Runs pip check, verifies skeletonkey's own import works, and reports
# the dependency status for every registered integration. This catches
# broken packages, missing optional SDKs, and import-time failures.
#
# Exit codes:
#   0  Clean dependency tree
#   1  Broken dependencies or import failures
#
# Usage:
#   bash scripts/ci_dependency_audit.sh [--json] [--sdk-status]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
JSON_OUT=false
SDK_STATUS=false

for arg in "$@"; do
    case "$arg" in
        --json)        JSON_OUT=true ;;
        --sdk-status)  SDK_STATUS=true ;;
        --help|-h)
            echo "Usage: $0 [--json] [--sdk-status]"
            echo ""
            echo "Audit dependencies: pip check, import check, SDK availability."
            echo ""
            echo "Options:"
            echo "  --json        Output raw JSON"
            echo "  --sdk-status  Report optional SDK availability for each integration"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$REPO_ROOT"
EXIT_CODE=0

if $JSON_OUT; then
    exec "$PYTHON" -c "
import importlib.util, json, subprocess, sys

result = {'pip_check': None, 'import_check': None, 'sdks': {}}

# pip check
try:
    proc = subprocess.run([sys.executable, '-m', 'pip', 'check'],
                          capture_output=True, text=True, timeout=30)
    result['pip_check'] = {'ok': proc.returncode == 0, 'output': proc.stdout.strip()}
except Exception as e:
    result['pip_check'] = {'ok': False, 'output': str(e)}

# import check
try:
    import skeletonkey
    result['import_check'] = {'ok': True, 'version': getattr(skeletonkey, '__version__', 'unknown')}
except Exception as e:
    result['import_check'] = {'ok': False, 'error': str(e)}

# SDK status
optional_sdks = {
    'boto3': 'aws-s3', 'pymongo': 'mongodb', 'psycopg2': 'postgresql',
    'redis': 'redis', 'requests': 'requests', 'github': 'github',
    'stripe': 'stripe',
}
for pkg, integration in sorted(optional_sdks.items()):
    spec = importlib.util.find_spec(pkg)
    result['sdks'][pkg] = {
        'available': spec is not None,
        'integration': integration,
    }

json.dump(result, sys.stdout, indent=2)
print()
"
fi

# Human-readable output
echo "=== pip check ==="
if PIP_OUT=$("$PYTHON" -m pip check 2>&1); then
    echo "  No broken dependencies."
else
    echo "  BROKEN DEPENDENCIES:" >&2
    echo "$PIP_OUT" | sed 's/^/    /' >&2
    EXIT_CODE=1
fi
echo ""

echo "=== import check ==="
if "$PYTHON" -c "import skeletonkey; print(f'  skeletonkey {skeletonkey.__version__} imported OK')" 2>/dev/null; then
    :
else
    echo "  IMPORT FAILED" >&2
    EXIT_CODE=1
fi
echo ""

if $SDK_STATUS; then
    echo "=== Optional SDK status ==="
    "$PYTHON" -c "
import importlib.util
sdks = {
    'boto3': 'aws-s3',
    'pymongo': 'mongodb',
    'psycopg2': 'postgresql',
    'redis': 'redis',
    'requests': 'requests',
    'github': 'github (PyGithub)',
    'stripe': 'stripe',
}
for pkg, integration in sorted(sdks.items()):
    spec = importlib.util.find_spec(pkg)
    status = 'installed' if spec else 'MISSING (integration blocked)'
    mark = '  ok' if spec else '  --'
    print(f'{mark}  {pkg:<15} -> {integration:<20} [{status}]')
"
    echo ""
fi

if [ "$EXIT_CODE" -eq 0 ]; then
    echo "Dependency audit passed."
fi
exit "$EXIT_CODE"
