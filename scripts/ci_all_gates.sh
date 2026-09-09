#!/usr/bin/env bash
# CI gate aggregator: run all gates in sequence
#
# This is the single entry point for running the full CI pipeline locally.
# It runs each gate script in order and reports a summary at the end.
# The pipeline is designed to fail fast: if a gate fails, subsequent gates
# still run (so you see all failures at once), but the exit code reflects
# the worst outcome.
#
# Gates (in order):
#   1. Lint (ruff + compileall) — fastest, catches syntax/style errors
#   2. Dependency audit — pip check + import check + SDK status
#   3. Integration validation — validate_registry()
#   4. Export audit — verify exports are clean
#   5. Compatibility matrix — SDK/OS readiness report
#   6. Test suite — full pytest
#   7. Doctor — sk doctor health check
#
# Exit codes:
#   0  All gates pass
#   1  One or more gates failed
#
# Usage:
#   bash scripts/ci_all_gates.sh [--skip TESTS] [--skip LINT] [--fast] [--json]
#
# The --fast flag skips the full test suite and doctor (useful during
# development when you just want the quick gates).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKIP_TESTS=false
SKIP_LINT=false
SKIP_DOCTOR=false
FAST=false
JSON_OUT=false
GATE_RESULTS=()

for arg in "$@"; do
    case "$arg" in
        --skip=tests)  SKIP_TESTS=true ;;
        --skip=lint)   SKIP_LINT=true ;;
        --skip=doctor) SKIP_DOCTOR=true ;;
        --fast)        FAST=true; SKIP_TESTS=true; SKIP_DOCTOR=true ;;
        --json)        JSON_OUT=true ;;
        --help|-h)
            echo "Usage: $0 [--skip tests|lint|doctor] [--fast] [--json]"
            echo ""
            echo "Run all CI gates in sequence."
            echo ""
            echo "Gates: lint -> deps -> validate -> export -> matrix -> tests -> doctor"
            echo ""
            echo "Options:"
            echo "  --skip=tests   Skip the full test suite"
            echo "  --skip=lint    Skip the lint gate"
            echo "  --skip=doctor  Skip the doctor gate"
            echo "  --fast         Skip tests and doctor (quick feedback)"
            echo "  --json         Output results as JSON"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

OVERALL_EXIT=0
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

run_gate() {
    local name="$1"
    local script="$2"
    shift 2

    if ! $JSON_OUT; then
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  GATE: $name"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    fi

    local gate_output
    local rc=0
    if $JSON_OUT; then
        gate_output=$(bash "$SCRIPT_DIR/$script" "$@" 2>&1) || rc=$?
    else
        bash "$SCRIPT_DIR/$script" "$@" || rc=$?
    fi

    if [ "$rc" -eq 0 ]; then
        if $JSON_OUT; then
            GATE_RESULTS+=("{\"gate\":\"$name\",\"status\":\"pass\"}")
        else
            echo "  >>> $name: PASS"
        fi
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        if $JSON_OUT; then
            GATE_RESULTS+=("{\"gate\":\"$name\",\"status\":\"fail\",\"exit_code\":$rc}")
        else
            echo "  >>> $name: FAIL (exit code $rc)"
        fi
        FAIL_COUNT=$((FAIL_COUNT + 1))
        OVERALL_EXIT=1
    fi
}

skip_gate() {
    local name="$1"
    if $JSON_OUT; then
        GATE_RESULTS+=("{\"gate\":\"$name\",\"status\":\"skip\"}")
    else
        echo "  >>> $name: SKIP"
    fi
    SKIP_COUNT=$((SKIP_COUNT + 1))
}

if ! $JSON_OUT; then
    echo "CI gate pipeline starting..."
fi

# Gate 1: Lint
if $SKIP_LINT; then
    skip_gate "lint"
else
    run_gate "lint" "ci_lint.sh"
fi

# Gate 2: Dependency audit
run_gate "dependency-audit" "ci_dependency_audit.sh" "--sdk-status"

# Gate 3: Integration validation
run_gate "validate-registry" "ci_validate_registry.sh"

# Gate 4: Export audit
run_gate "export-audit" "ci_export_audit.sh"

# Gate 5: Compatibility matrix
run_gate "compatibility-matrix" "ci_compatibility_matrix.sh"

# Gate 6: Test suite
if $SKIP_TESTS; then
    skip_gate "test-suite"
else
    run_gate "test-suite" "ci_test.sh" "--full"
    # Clean up any state the test suite left in the repo working tree.
    # Some tests (doctor, registry-config) deliberately test the default
    # state path and leave a partial .sk behind. The doctor gate expects
    # a clean working tree, so we clean up here.
    rm -rf "${REPO_ROOT}/.sk"
fi

# Gate 7: Doctor
if $SKIP_DOCTOR; then
    skip_gate "doctor"
else
    run_gate "doctor" "ci_doctor.sh"
fi

# Summary
if $JSON_OUT; then
    GATES_JSON=$(IFS=,; echo "${GATE_RESULTS[*]}")
    echo "{\"gates\":[$GATES_JSON],\"pass\":$PASS_COUNT,\"fail\":$FAIL_COUNT,\"skip\":$SKIP_COUNT,\"overall\":\"$([ $OVERALL_EXIT -eq 0 ] && echo pass || echo fail)\"}"
else
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  SUMMARY: $PASS_COUNT pass, $FAIL_COUNT fail, $SKIP_COUNT skip"
    echo ""
    if [ "$OVERALL_EXIT" -eq 0 ]; then
        echo "  All gates passed."
    else
        echo "  SOME GATES FAILED. Review output above."
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

exit "$OVERALL_EXIT"
