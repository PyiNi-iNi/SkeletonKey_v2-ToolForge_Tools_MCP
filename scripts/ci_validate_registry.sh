#!/usr/bin/env bash
# CI gate: integration registry validation
#
# Runs validate_registry() and reports every integration's validation status.
# This is the primary release gate: a release should have zero errors across
# all integrations. Secret findings are warnings but a release should have
# zero of those too.
#
# Exit codes:
#   0  All integrations valid, zero secret findings
#   1  One or more integration validation errors
#   2  Secret findings (warnings) but no hard errors
#
# Usage:
#   bash scripts/ci_validate_registry.sh [--json] [--include-health] [--name NAME]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
JSON_OUT=false
INCLUDE_HEALTH=false
FILTER_NAME=""
EXIT_CODE=0

for arg in "$@"; do
    case "$arg" in
        --json)           JSON_OUT=true ;;
        --include-health) INCLUDE_HEALTH=true ;;
        --name=*)         FILTER_NAME="${arg#--name=}" ;;
        --help|-h)
            echo "Usage: $0 [--json] [--include-health] [--name NAME]"
            echo ""
            echo "Run validate_registry() and report integration validation status."
            echo ""
            echo "Options:"
            echo "  --json            Output raw JSON instead of human-readable report"
            echo "  --include-health  Also run health checks on each integration"
            echo "  --name=NAME       Validate only this integration (default: all)"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$REPO_ROOT"

if $JSON_OUT; then
    exec "$PYTHON" -c "
import json, sys
sys.path.insert(0, '.')
from skeletonkey.integrations.validation import validate_registry, validate_integration

name_filter = '${FILTER_NAME}'
if name_filter:
    reports = [validate_integration(name_filter, include_health=${INCLUDE_HEALTH})]
else:
    reports = validate_registry()

output = {
    'integrations': [r.to_dict() for r in reports],
    'total': len(reports),
    'valid': sum(1 for r in reports if r.valid),
    'invalid': sum(1 for r in reports if not r.valid),
    'total_errors': sum(len(r.errors) for r in reports),
    'total_warnings': sum(len(r.warnings) for r in reports),
    'secret_findings': sum(
        r.checks.get('secrets', 0) if isinstance(r.checks.get('secrets', 0), int) else 0
        for r in reports
    ),
}
json.dump(output, sys.stdout, indent=2)
print()
"
else
    "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from skeletonkey.integrations.validation import validate_registry, validate_integration

name_filter = '${FILTER_NAME}'
if name_filter:
    reports = [validate_integration(name_filter, include_health=${INCLUDE_HEALTH})]
else:
    reports = validate_registry()

total_errors = 0
total_warnings = 0
total_secret_findings = 0

for r in reports:
    errors = r.errors
    warnings = r.warnings
    total_errors += len(errors)
    total_warnings += len(warnings)
    secrets = r.checks.get('secrets', 0)
    if isinstance(secrets, int):
        total_secret_findings += secrets

    status = 'PASS' if r.valid else 'FAIL'
    tools = r.checks.get('tools', '?')
    print(f'  [{status}] {r.name:<20} tools={tools}  errors={len(errors)}  warnings={len(warnings)}  secrets={secrets}')

    for e in errors:
        print(f'         ERROR  {e.code}: {e.message}')
        if e.detail:
            print(f'                {e.detail}')
    for w in warnings:
        print(f'         WARN   {w.code}: {w.message}')
        if w.detail:
            print(f'                {w.detail}')

print()
print(f'  {len(reports)} integrations validated: '
      f'{sum(1 for r in reports if r.valid)} valid, '
      f'{sum(1 for r in reports if not r.valid)} invalid')
print(f'  {total_errors} error(s), {total_warnings} warning(s), {total_secret_findings} secret finding(s)')

if total_errors > 0:
    sys.exit(1)
elif total_secret_findings > 0:
    sys.exit(2)
sys.exit(0)
"
fi
