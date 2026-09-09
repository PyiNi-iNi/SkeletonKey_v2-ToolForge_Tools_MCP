#!/usr/bin/env bash
# CI gate: export format audit
#
# Verifies that all export formats produce valid, credential-free output.
# This catches accidental secret leaks in exported tool schemas and ensures
# each format produces well-formed JSON with the expected structure.
#
# Exit codes:
#   0  All formats export cleanly
#   1  Export failure or credential detected
#
# Usage:
#   bash scripts/ci_export_audit.sh [--format FORMAT] [--json] [--tier TIER]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
FORMAT=""
TIER=""
JSON_OUT=false

for arg in "$@"; do
    case "$arg" in
        --format=*)  FORMAT="${arg#--format=}" ;;
        --tier=*)    TIER="${arg#--tier=}" ;;
        --json)      JSON_OUT=true ;;
        --help|-h)
            echo "Usage: $0 [--format FORMAT] [--tier TIER] [--json]"
            echo ""
            echo "Audit export formats for correctness and secret leakage."
            echo ""
            echo "Options:"
            echo "  --format=FORMAT  Test only this format (default: all)"
            echo "  --tier=TIER      Export from this tier (default: full)"
            echo "  --json           Output raw JSON"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$REPO_ROOT"

"$PYTHON" -c "
import json, sys, re
sys.path.insert(0, '.')
from skeletonkey.core.export import export_from_registry, list_formats, _CREDENTIAL_PATTERNS
from skeletonkey.toolkit import build

formats = ['${FORMAT}'] if '${FORMAT}' else list_formats()
tier = '${TIER}' or None

tk = build()
exit_code = 0

# Credential patterns for the audit
patterns = [
    re.compile(r'ghp_[A-Za-z0-9]{36,}'),
    re.compile(r'sk-(live|test|proj)-[A-Za-z0-9]{20,}'),
    re.compile(r'xox[baprs]-[A-Za-z0-9-]+'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'eyJ[A-Za-z0-9_-]+\.'),
]

for fmt in formats:
    try:
        result = export_from_registry(tk.engine.registry, fmt, tier=tier)
        serialized = json.dumps(result)
        count = result['count']

        # Check for credential leakage
        leaks = []
        for pat in patterns:
            matches = pat.findall(serialized)
            if matches:
                leaks.extend(matches)

        if leaks:
            print(f'  FAIL  {fmt}: {count} tools exported, but {len(leaks)} credential pattern(s) detected!')
            for leak in leaks[:5]:
                print(f'        leaked: {leak[:20]}...')
            exit_code = 1
        else:
            print(f'  ok    {fmt}: {count} tools exported, no credential leakage')

    except Exception as exc:
        print(f'  FAIL  {fmt}: export failed: {exc}')
        exit_code = 1

tk.close()

if exit_code == 0:
    print()
    print('Export audit passed.')

sys.exit(exit_code)
"
