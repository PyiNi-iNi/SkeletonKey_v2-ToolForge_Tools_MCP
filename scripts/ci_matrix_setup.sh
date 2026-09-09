#!/usr/bin/env bash
# P6/P5.5 CI matrix readiness script.
#
# Prepares an environment for the full integration and compatibility matrix
# tests. Designed for CI runners (Linux, Windows via Git Bash / WSL, macOS)
# and local development alike.
#
# Usage:
#   bash scripts/ci_matrix_setup.sh [--sdk-only] [--matrix-only] [--audit-only] [--gen-docs]
#
# Sections:
#   1. Optional SDK installation (boto3, pymongo, psycopg2-binary, redis, requests)
#   2. OS-specific setup (Windows runners, macOS Homebrew, Linux apt/dnf)
#   3. Dependency audit (pip check + skeletonkey dependency_status)
#   4. Generated API reference + integration templates
#
# Exit codes:
#   0  All requested sections succeeded
#   1  A required section failed
#   2  An optional section had warnings (non-blocking)

set -euo pipefail

# ---- Configuration ---------------------------------------------------------

PYTHON="${PYTHON:-python3}"
PIP="${PYTHON} -m pip"
VENV_DIR="${VENV_DIR:-.venv}"
MATRIX_REPORT="${MATRIX_REPORT:-/tmp/sk-compatibility-matrix.json}"
API_DOCS_DIR="${API_DOCS_DIR:-docs/api-reference}"
TEMPLATES_DIR="${TEMPLATES_DIR:-docs/templates}"
EXIT_CODE=0

# Optional SDK packages (names as on PyPI). Each is optional: the integration
# loads without it but the compatibility matrix reports it as "blocked".
OPTIONAL_SDKS=(
    "boto3"
    "pymongo"
    "psycopg2-binary"
    "redis"
    "requests"
)

# ---- Helpers ---------------------------------------------------------------

info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; EXIT_CODE=2; }
fail()  { printf '\033[1;31m[FAIL]\033[0m  %s\n' "$*"; EXIT_CODE=1; }
ok()    { printf '\033[1;32m[OK]\033[0m    %s\n' "$*"; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { warn "$1 not found; skipping $2"; return 1; }
}

detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux"   ;;
        Darwin*) echo "darwin"  ;;
        MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
        *)       echo "unknown" ;;
    esac
}

# Activate venv if it exists
activate_venv() {
    if [ -d "$VENV_DIR" ]; then
        info "Activating virtualenv at $VENV_DIR"
        # shellcheck disable=SC1091
        source "$VENV_DIR/bin/activate" 2>/dev/null \
            || source "$VENV_DIR/Scripts/activate" 2>/dev/null \
            || { fail "Cannot activate venv"; return 1; }
        PYTHON="python"
        PIP="python -m pip"
    fi
}

# ---- Section 1: Optional SDKs ----------------------------------------------

install_optional_sdks() {
    info "=== Section 1: Optional SDK installation ==="

    local installed=0
    local failed_list=()

    for pkg in "${OPTIONAL_SDKS[@]}"; do
        if $PYTHON -c "import importlib.util; exit(0 if importlib.util.find_spec('$pkg') else 1)" 2>/dev/null; then
            ok "$pkg already installed"
            ((installed++)) || true
        else
            info "Installing $pkg..."
            if $PIP install --quiet "$pkg" 2>/dev/null; then
                ok "$pkg installed"
                ((installed++)) || true
            else
                warn "$pkg installation failed (non-blocking)"
                failed_list+=("$pkg")
            fi
        fi
    done

    info "SDK status: $installed/${#OPTIONAL_SDKS[@]} available"
    if [ ${#failed_list[@]} -gt 0 ]; then
        warn "Missing SDKs: ${failed_list[*]}"
        warn "Integrations requiring these will report 'blocked' in the compatibility matrix"
    fi
}

# ---- Section 2: OS-specific setup ------------------------------------------

os_setup_linux() {
    info "=== Section 2: Linux runner setup ==="

    # PostgreSQL client libraries (for psycopg2)
    if require_cmd apt-get "apt-based setup"; then
        info "Debian/Ubuntu detected; installing system dependencies"
        if [ "$(id -u)" = "0" ]; then
            apt-get update -qq
            apt-get install -y -qq libpq-dev libffi-dev 2>/dev/null || warn "apt install had warnings"
        else
            warn "Not running as root; skipping system package install"
            warn "Install libpq-dev libffi-dev manually for full psycopg2 support"
        fi
    elif require_cmd dnf "dnf-based setup"; then
        info "Fedora/RHEL detected"
        if [ "$(id -u)" = "0" ]; then
            dnf install -q -y libpq-devel libffi-devel 2>/dev/null || warn "dnf install had warnings"
        fi
    fi
}

os_setup_darwin() {
    info "=== Section 2: macOS runner setup ==="

    if require_cmd brew "Homebrew setup"; then
        info "Installing PostgreSQL client libraries via Homebrew"
        brew install --quiet postgresql@16 libffi 2>/dev/null || warn "brew install had warnings"
    else
        warn "Homebrew not found; psycopg2 may not compile without libpq"
    fi
}

os_setup_windows() {
    info "=== Section 2: Windows runner setup ==="

    # Windows: most optional SDKs install as pure-Python wheels.
    # psycopg2-binary ships its own libpq; no system deps needed.
    ok "Windows: all optional SDKs ship as wheels; no system setup needed"

    # Verify PowerShell is available for cross-platform shell tests
    if command -v pwsh >/dev/null 2>&1; then
        ok "pwsh available for cross-platform shell tests"
    else
        warn "pwsh not found; cross-platform shell tests will be skipped"
    fi
}

run_os_setup() {
    local os
    os="$(detect_os)"
    case "$os" in
        linux)   os_setup_linux   ;;
        darwin)  os_setup_darwin  ;;
        windows) os_setup_windows ;;
        *)       warn "Unknown OS ($os); skipping OS-specific setup" ;;
    esac
}

# ---- Section 3: Dependency audit -------------------------------------------

run_dependency_audit() {
    info "=== Section 3: Dependency audit ==="

    # pip check: verify no broken dependencies
    info "Running pip check..."
    if $PIP check 2>/dev/null; then
        ok "pip check: no broken dependencies"
    else
        fail "pip check: broken dependencies detected"
    fi

    # SkeletonKey compatibility matrix
    info "Generating compatibility matrix..."
    if $PYTHON -c "
import json, sys
sys.path.insert(0, '.')
from skeletonkey.integrations.validation import compatibility_matrix
matrix = compatibility_matrix()
with open('${MATRIX_REPORT}', 'w') as f:
    json.dump(matrix, f, indent=2)
ready = sum(1 for r in matrix if r.get('status') == 'ready')
blocked = sum(1 for r in matrix if r.get('status') == 'blocked')
print(f'Matrix: {ready} ready, {blocked} blocked, {len(matrix)} total rows')
" 2>/dev/null; then
        ok "Compatibility matrix written to ${MATRIX_REPORT}"
    else
        warn "Could not generate compatibility matrix (integrations may not be importable yet)"
    fi

    # Validate the integration registry
    info "Validating integration registry..."
    if $PYTHON -c "
import sys
sys.path.insert(0, '.')
from skeletonkey.integrations.validation import validate_registry
reports = validate_registry()
errors = sum(len(r.errors) for r in reports)
warnings = sum(len(r.warnings) for r in reports)
print(f'Registry: {len(reports)} integrations, {errors} errors, {warnings} warnings')
sys.exit(1 if errors > 0 else 0)
" 2>/dev/null; then
        ok "Integration registry validation passed"
    else
        fail "Integration registry has validation errors"
    fi
}

# ---- Section 4: Generated docs and templates -------------------------------

generate_api_reference() {
    info "=== Section 4: Generated API reference and templates ==="

    mkdir -p "$API_DOCS_DIR" "$TEMPLATES_DIR"

    # Generate integration API reference from the live registry
    if $PYTHON -c "
import json, sys, os
sys.path.insert(0, '.')
from skeletonkey.integrations import registry

lines = []
lines.append('# Integration API Reference')
lines.append('')
lines.append('Generated from the live integration registry.')
lines.append('')

for info in sorted(registry.list_integrations(), key=lambda i: i.name):
    d = info.to_dict()
    lines.append(f'## {d.get(\"title\", d[\"name\"])}')
    lines.append('')
    lines.append(f'- **Name:** \`{d[\"name\"]}\`')
    lines.append(f'- **Version:** {d.get(\"version\", \"unknown\")}')
    lines.append(f'- **Type:** {d.get(\"type\", \"unknown\")}')
    lines.append(f'- **Category:** {d.get(\"category\", \"unknown\")}')
    lines.append(f'- **Platform:** {d.get(\"platform\", \"cross-platform\")}')
    lines.append(f'- **Description:** {d.get(\"description\", \"\")}')
    deps = d.get('dependencies', [])
    if deps:
        dep_names = [dep['name'] for dep in deps if isinstance(dep, dict)]
        lines.append(f'- **Dependencies:** {\", \".join(dep_names) if dep_names else \"none\"}')
    caps = d.get('capabilities', [])
    if caps:
        lines.append(f'- **Capabilities:** {\", \".join(caps)}')
    lines.append('')

path = os.path.join('${API_DOCS_DIR}', 'INTEGRATIONS.md')
with open(path, 'w') as f:
    f.write('\n'.join(lines))
print(f'Wrote {len(registry.list_integrations())} integration entries to {path}')
" 2>/dev/null; then
        ok "API reference generated at ${API_DOCS_DIR}/INTEGRATIONS.md"
    else
        warn "Could not generate API reference"
    fi

    # Generate integration template
    cat > "${TEMPLATES_DIR}/integration_template.py" << 'TEMPLATE'
"""{{INTEGRATION_NAME}} Integration for SkeletonKey.

{{DESCRIPTION}}
"""

from __future__ import annotations

from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.integrations import (
    Integration,
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationPlatform,
    IntegrationType,
    register_integration,
)


@register_integration(
    name="{{INTEGRATION_NAME}}",
    title="{{TITLE}}",
    version="1.0.0",
    integration_type=IntegrationType.API,
    category=IntegrationCategory.OTHER,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="{{DESCRIPTION}}",
    author="Your Name",
    license="Apache-2.0",
    keywords=[],
    dependencies=[],
    capabilities=[],
)
class {{CLASS_NAME}}Integration(Integration):
    """{{DESCRIPTION}}"""

    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
        # Initialize provider client here.

    def initialize(self) -> bool:
        self._initialized = True
        return True

    def cleanup(self) -> None:
        self._initialized = False

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "{{INTEGRATION_NAME}}.example",
                "description": "An example tool",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query"},
                    },
                    "required": ["query"],
                },
            },
        ]

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        suffix = name.split(".", 1)[-1] if "." in name else name
        method = getattr(self, f"tool_{suffix}", None)
        if method is None:
            raise SkeletonKeyError(E.UNKNOWN_TOOL, f"Unknown tool: {name}")
        return method(**args)

    def tool_example(self, query: str) -> dict[str, Any]:
        return {"result": f"Example result for {query!r}"}

    def check_health(self) -> tuple[str, str]:
        if self._initialized:
            return ("healthy", "Integration is ready")
        return ("unhealthy", "Integration not initialized")
TEMPLATE
    ok "Integration template written to ${TEMPLATES_DIR}/integration_template.py"

    # Generate REST integration template
    cat > "${TEMPLATES_DIR}/rest_integration_template.py" << 'TEMPLATE'
"""{{INTEGRATION_NAME}} REST Integration for SkeletonKey.

{{DESCRIPTION}}

Uses the shared RestIntegration base for stdlib urllib transport,
authentication hooks, error normalization, and pagination.
"""

from __future__ import annotations

from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.integrations import (
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationPlatform,
    IntegrationType,
    RestIntegration,
    register_integration,
)

DEFAULT_BASE_URL = "{{BASE_URL}}"


@register_integration(
    name="{{INTEGRATION_NAME}}",
    title="{{TITLE}}",
    version="1.0.0",
    integration_type=IntegrationType.API,
    category=IntegrationCategory.OTHER,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="{{DESCRIPTION}}",
    author="Your Name",
    license="Apache-2.0",
    keywords=[],
    dependencies=[],
    capabilities=[],
    conflicts=[],
    metadata={"min_engine": ">=0.2.0"},
)
class {{CLASS_NAME}}Integration(RestIntegration):
    """{{DESCRIPTION}}"""

    default_base_url = DEFAULT_BASE_URL

    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
        self._token = str(
            config.config.get("token")
            or config.secrets.get("token", "")
            if hasattr(config, "secrets")
            else config.config.get("token", "")
        )

    def _auth_headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _remote_error(self, response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        data = response.get("data") or {}
        message = data.get("message") or data.get("error") or "request failed"
        return (str(message), {"provider_code": data.get("code", "unknown")})

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "{{INTEGRATION_NAME}}.list_items",
                "description": "List items from the API",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "description": "Page number"},
                        "per_page": {"type": "integer", "description": "Items per page"},
                    },
                },
            },
        ]

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        suffix = name.split(".", 1)[-1] if "." in name else name
        method = getattr(self, f"tool_{suffix}", None)
        if method is None:
            raise SkeletonKeyError(E.UNKNOWN_TOOL, f"Unknown tool: {name}")
        return method(**args)

    def tool_list_items(self, page: int = 1, per_page: int = 20) -> dict[str, Any]:
        response = self._api("GET", "/items", query={"page": page, "per_page": per_page})
        return self._envelope(response)

    def check_health(self) -> tuple[str, str]:
        if self._initialized:
            return ("healthy", "REST integration is ready")
        return ("unhealthy", "Integration not initialized")
TEMPLATE
    ok "REST integration template written to ${TEMPLATES_DIR}/rest_integration_template.py"
}

# ---- Main ------------------------------------------------------------------

main() {
    local run_sdks=true run_matrix=true run_audit=true run_docs=true

    for arg in "$@"; do
        case "$arg" in
            --sdk-only)    run_matrix=false; run_audit=false; run_docs=false ;;
            --matrix-only) run_sdks=false;  run_audit=false; run_docs=false ;;
            --audit-only)  run_sdks=false;  run_matrix=false; run_docs=false ;;
            --gen-docs)    run_sdks=false;  run_matrix=false; run_audit=false ;;
            --help|-h)
                echo "Usage: $0 [--sdk-only] [--matrix-only] [--audit-only] [--gen-docs]"
                echo ""
                echo "Sections (default: all):"
                echo "  --sdk-only     Install optional SDK packages only"
                echo "  --matrix-only  Generate the compatibility matrix only"
                echo "  --audit-only   Run dependency audit only"
                echo "  --gen-docs     Generate API reference and templates only"
                exit 0
                ;;
            *)
                fail "Unknown argument: $arg"
                exit 1
                ;;
        esac
    done

    info "P6/P5.5 CI matrix readiness script"
    info "OS: $(detect_os) | Python: $($PYTHON --version 2>&1)"
    echo ""

    activate_venv || true

    if $run_sdks; then
        install_optional_sdks
        run_os_setup
        echo ""
    fi

    if $run_matrix || $run_audit; then
        run_dependency_audit
        echo ""
    fi

    if $run_docs; then
        generate_api_reference
        echo ""
    fi

    case "$EXIT_CODE" in
        0) ok "All sections completed successfully" ;;
        1) fail "One or more required sections failed" ;;
        2) warn "Completed with warnings" ;;
    esac

    exit "$EXIT_CODE"
}

main "$@"
