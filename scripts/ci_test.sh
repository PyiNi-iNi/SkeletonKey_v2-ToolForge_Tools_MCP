#!/usr/bin/env bash
# CI gate: full test suite
#
# Runs pytest with the full test suite and reports results.
# Supports running specific test categories for faster feedback.
#
# Exit codes:
#   0  All tests passed (skips/xfails are allowed)
#   1  One or more test failures
#
# Usage:
#   bash scripts/ci_test.sh [--full] [--integration] [--p7] [--fast] [-k EXPR]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
MODE="full"
EXTRA_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --full)        MODE="full" ;;
        --integration) MODE="integration" ;;
        --p7)          MODE="p7" ;;
        --fast)        MODE="fast" ;;
        -k)            MODE="custom"; EXTRA_ARGS="-k" ;;
        --help|-h)
            echo "Usage: $0 [--full] [--integration] [--p7] [--fast] [-k EXPR]"
            echo ""
            echo "Run the pytest test suite."
            echo ""
            echo "Modes:"
            echo "  --full         All tests (default, ~80s)"
            echo "  --integration  Integration tests only (loader, validation, rest, packs)"
            echo "  --p7           P7 exploratory tests only (export, planner, quarantine, subagent, remote)"
            echo "  --fast         Core tests only (skip slow MCP/wire tests)"
            echo "  -k EXPR        Run tests matching pytest -k expression"
            exit 0
            ;;
        *)
            if [ "$MODE" = "custom" ] && [ "$EXTRA_ARGS" = "-k" ]; then
                EXTRA_ARGS="-k $arg"
                MODE="custom_k"
            else
                echo "Unknown option: $arg" >&2
                exit 1
            fi
            ;;
    esac
done

cd "$REPO_ROOT"

# Find pytest
PYTEST="${REPO_ROOT}/.venv/bin/pytest"
if [ ! -x "$PYTEST" ]; then
    PYTEST="$(command -v pytest 2>/dev/null || true)"
fi
if [ -z "$PYTEST" ] || [ ! -x "$PYTEST" ]; then
    echo "pytest not found. Install with: pip install -e '.[dev]'" >&2
    exit 1
fi

case "$MODE" in
    full)
        echo "=== Full test suite ==="
        "$PYTEST" tests/ -q
        ;;
    integration)
        echo "=== Integration tests ==="
        "$PYTEST" tests/test_integration_loader.py \
                  tests/test_integration_validation.py \
                  tests/test_integration_packs.py \
                  tests/test_rest_integration.py \
                  -q
        ;;
    p7)
        echo "=== P7 exploratory tests ==="
        "$PYTEST" tests/test_export.py \
                  tests/test_planner.py \
                  tests/test_quarantine.py \
                  tests/test_subagent.py \
                  tests/test_remote_targets.py \
                  tests/test_p7_engine_integration.py \
                  -q
        ;;
    fast)
        echo "=== Fast test suite (excluding slow MCP/wire) ==="
        "$PYTEST" tests/ -q --ignore=tests/test_mcp_stdio.py --ignore=tests/test_wire.py
        ;;
    custom_k)
        echo "=== Custom test selection: $EXTRA_ARGS ==="
        "$PYTEST" tests/ -q $EXTRA_ARGS
        ;;
esac
