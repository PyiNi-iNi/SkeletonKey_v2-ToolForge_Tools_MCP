#!/usr/bin/env bash
# CI gate: lint + compile check
#
# Runs ruff and python -m compileall to verify code quality.
# This is the fastest gate — run it first to catch syntax and style errors
# before investing time in longer tests.
#
# Exit codes:
#   0  All checks pass
#   1  Ruff found issues or compile failed
#
# Usage:
#   bash scripts/ci_lint.sh [--ruff-only] [--compile-only]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
RUN_RUFF=true
RUN_COMPILE=true

for arg in "$@"; do
    case "$arg" in
        --ruff-only)    RUN_COMPILE=false ;;
        --compile-only) RUN_RUFF=false ;;
        --help|-h)
            echo "Usage: $0 [--ruff-only] [--compile-only]"
            echo ""
            echo "Run ruff lint and python compileall checks."
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$REPO_ROOT"
EXIT_CODE=0

if $RUN_RUFF; then
    echo "=== ruff check ==="
    if RUFF_BIN="$(command -v ruff 2>/dev/null)" || RUFF_BIN="${REPO_ROOT}/.venv/bin/ruff"; then
        if [ -x "$RUFF_BIN" ]; then
            if "$RUFF_BIN" check .; then
                echo "  ruff: clean"
            else
                echo "  ruff: ISSUES FOUND" >&2
                EXIT_CODE=1
            fi
        else
            echo "  ruff: not found (skipping)" >&2
        fi
    else
        echo "  ruff: not found (skipping)" >&2
    fi
    echo ""
fi

if $RUN_COMPILE; then
    echo "=== compileall ==="
    FAILED=0
    for pkg in skeletonkey tests; do
        if [ -d "$pkg" ]; then
            if "$PYTHON" -m compileall -q "$pkg" 2>/dev/null; then
                echo "  $pkg: clean"
            else
                echo "  $pkg: COMPILE ERRORS" >&2
                FAILED=1
            fi
        fi
    done
    if [ "$FAILED" -eq 1 ]; then
        EXIT_CODE=1
    fi
    echo ""
fi

if [ "$EXIT_CODE" -eq 0 ]; then
    echo "All lint checks passed."
fi
exit "$EXIT_CODE"
