"""CLI Auto-Discovery for SkeletonKey Integrations (P5.7).

This module detects common CLI tools on the system PATH and generates
SkeletonKey integration wrappers for them automatically.

Supports: git, docker, aws, gh, kubectl, terraform, npm, pip, curl, jq, etc.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationPlatform,
    IntegrationType,
    register_integration,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CLI Tool Definitions
# =============================================================================

@dataclass
class CLIToolSpec:
    """Specification for a known CLI tool."""
    name: str
    title: str
    description: str
    category: IntegrationCategory = IntegrationCategory.DEVTOOLS
    version_flag: str = "--version"
    version_regex: str = r"(\d+\.\d+\.\d+)"
    subcommands: dict[str, dict[str, Any]] = field(default_factory=dict)
    global_flags: list[dict[str, Any]] = field(default_factory=list)
    risk_by_subcommand: dict[str, str] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)


# Known CLI tools with their specifications
KNOWN_CLI_TOOLS: dict[str, CLIToolSpec] = {
    "git": CLIToolSpec(
        name="git",
        title="Git",
        description="Distributed version control system",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"git version (\d+\.\d+\.\d+)",
        subcommands={
            "status": {"description": "Show working tree status", "risk": "read"},
            "add": {"description": "Add file contents to index", "risk": "write"},
            "commit": {"description": "Record changes to repository", "risk": "write"},
            "push": {"description": "Update remote refs", "risk": "network"},
            "pull": {"description": "Fetch and merge", "risk": "network"},
            "fetch": {"description": "Download objects from remote", "risk": "network"},
            "clone": {"description": "Clone a repository", "risk": "write"},
            "branch": {"description": "List/create/delete branches", "risk": "read"},
            "checkout": {"description": "Switch branches or restore files", "risk": "write"},
            "merge": {"description": "Join histories", "risk": "write"},
            "rebase": {"description": "Reapply commits", "risk": "write"},
            "log": {"description": "Show commit logs", "risk": "read"},
            "diff": {"description": "Show changes", "risk": "read"},
            "stash": {"description": "Stash changes", "risk": "write"},
            "remote": {"description": "Manage remotes", "risk": "read"},
            "tag": {"description": "Manage tags", "risk": "read"},
        },
        global_flags=[
            {"name": "c", "type": "string", "description": "Run as if git was started in <path>"},
            {"name": "config", "type": "string", "description": "Set config variable"},
        ],
        risk_by_subcommand={
            "status": "read", "log": "read", "diff": "read", "branch": "read",
            "show": "read", "remote": "read", "tag": "read", "ls-files": "read",
            "add": "write", "commit": "write", "checkout": "write", "merge": "write",
            "rebase": "write", "reset": "write", "stash": "write",
            "push": "network", "pull": "network", "fetch": "network", "clone": "network",
        },
        examples=[
            "git status",
            "git commit -m 'message'",
            "git push origin main",
            "git diff HEAD~1",
        ],
    ),

    "docker": CLIToolSpec(
        name="docker",
        title="Docker",
        description="Container platform",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"Docker version (\d+\.\d+\.\d+)",
        subcommands={
            "ps": {"description": "List containers", "risk": "read"},
            "images": {"description": "List images", "risk": "read"},
            "run": {"description": "Run a container", "risk": "write"},
            "build": {"description": "Build an image", "risk": "write"},
            "pull": {"description": "Pull an image", "risk": "network"},
            "push": {"description": "Push an image", "risk": "network"},
            "exec": {"description": "Execute command in container", "risk": "write"},
            "logs": {"description": "Fetch container logs", "risk": "read"},
            "stop": {"description": "Stop container", "risk": "write"},
            "start": {"description": "Start container", "risk": "write"},
            "restart": {"description": "Restart container", "risk": "write"},
            "rm": {"description": "Remove container", "risk": "destructive"},
            "rmi": {"description": "Remove image", "risk": "destructive"},
            "network": {"description": "Manage networks", "risk": "write"},
            "volume": {"description": "Manage volumes", "risk": "write"},
            "compose": {"description": "Docker Compose", "risk": "write"},
        },
        global_flags=[
            {"name": "host", "type": "string", "description": "Daemon socket to connect to"},
            {"name": "config", "type": "string", "description": "Config file location"},
        ],
        risk_by_subcommand={
            "ps": "read", "images": "read", "logs": "read", "inspect": "read",
            "run": "write", "build": "write", "start": "write", "exec": "write",
            "pull": "network", "push": "network",
            "stop": "write", "restart": "write", "rm": "destructive", "rmi": "destructive",
        },
        examples=[
            "docker ps -a",
            "docker run -d nginx",
            "docker build -t myapp .",
            "docker logs container_name",
        ],
    ),

    "aws": CLIToolSpec(
        name="aws",
        title="AWS CLI",
        description="Amazon Web Services command line interface",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"aws-cli/(\d+\.\d+\.\d+)",
        subcommands={
            "s3": {"description": "S3 operations", "risk": "write"},
            "ec2": {"description": "EC2 operations", "risk": "write"},
            "lambda": {"description": "Lambda operations", "risk": "write"},
            "dynamodb": {"description": "DynamoDB operations", "risk": "write"},
            "cloudformation": {"description": "CloudFormation operations", "risk": "write"},
            "iam": {"description": "IAM operations", "risk": "write"},
            "sts": {"description": "Security Token Service", "risk": "read"},
        },
        global_flags=[
            {"name": "region", "type": "string", "description": "AWS region"},
            {"name": "profile", "type": "string", "description": "Credentials profile"},
            {"name": "output", "type": "string", "description": "Output format (json, text, table)"},
        ],
        risk_by_subcommand={"*": "write"},
        examples=[
            "aws s3 ls",
            "aws ec2 describe-instances",
            "aws lambda list-functions",
        ],
    ),

    "gh": CLIToolSpec(
        name="gh",
        title="GitHub CLI",
        description="GitHub command line interface",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"gh version (\d+\.\d+\.\d+)",
        subcommands={
            "repo": {"description": "Repository operations", "risk": "write"},
            "pr": {"description": "Pull request operations", "risk": "write"},
            "issue": {"description": "Issue operations", "risk": "write"},
            "release": {"description": "Release operations", "risk": "write"},
            "workflow": {"description": "GitHub Actions workflows", "risk": "read"},
            "run": {"description": "Workflow run operations", "risk": "read"},
            "api": {"description": "Raw API calls", "risk": "network"},
            "auth": {"description": "Authentication", "risk": "write"},
        },
        global_flags=[
            {"name": "repo", "type": "string", "description": "Repository (owner/name)"},
            {"name": "json", "type": "boolean", "description": "Output as JSON"},
        ],
        risk_by_subcommand={
            "api": "network", "repo view": "read", "pr view": "read", "issue view": "read",
            "workflow view": "read", "run view": "read",
            "repo create": "write", "pr create": "write", "issue create": "write",
            "release create": "write",
        },
        examples=[
            "gh repo view owner/repo",
            "gh pr list",
            "gh issue create --title 'Bug' --body 'Description'",
            "gh api /users/me",
        ],
    ),

    "kubectl": CLIToolSpec(
        name="kubectl",
        title="Kubernetes CLI",
        description="Kubernetes command line tool",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="version --client",
        version_regex=r"Client Version: v(\d+\.\d+\.\d+)",
        subcommands={
            "get": {"description": "Display resources", "risk": "read"},
            "describe": {"description": "Show resource details", "risk": "read"},
            "logs": {"description": "Print container logs", "risk": "read"},
            "exec": {"description": "Execute in container", "risk": "write"},
            "apply": {"description": "Apply configuration", "risk": "write"},
            "create": {"description": "Create resource", "risk": "write"},
            "delete": {"description": "Delete resource", "risk": "destructive"},
            "rollout": {"description": "Manage rollouts", "risk": "write"},
            "scale": {"description": "Scale resources", "risk": "write"},
            "port-forward": {"description": "Forward ports", "risk": "network"},
            "proxy": {"description": "Run proxy", "risk": "network"},
        },
        global_flags=[
            {"name": "namespace", "type": "string", "description": "Namespace"},
            {"name": "context", "type": "string", "description": "Kubeconfig context"},
            {"name": "kubeconfig", "type": "string", "description": "Kubeconfig file"},
        ],
        risk_by_subcommand={
            "get": "read", "describe": "read", "logs": "read", "top": "read",
            "apply": "write", "create": "write", "patch": "write", "replace": "write",
            "delete": "destructive", "exec": "write", "port-forward": "network", "proxy": "network",
        },
        examples=[
            "kubectl get pods",
            "kubectl logs -f deployment/myapp",
            "kubectl apply -f deployment.yaml",
        ],
    ),

    "terraform": CLIToolSpec(
        name="terraform",
        title="Terraform",
        description="Infrastructure as Code tool",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="version",
        version_regex=r"Terraform v(\d+\.\d+\.\d+)",
        subcommands={
            "init": {"description": "Initialize working directory", "risk": "write"},
            "plan": {"description": "Show execution plan", "risk": "read"},
            "apply": {"description": "Apply changes", "risk": "write"},
            "destroy": {"description": "Destroy infrastructure", "risk": "destructive"},
            "validate": {"description": "Validate configuration", "risk": "read"},
            "fmt": {"description": "Format configuration", "risk": "write"},
            "output": {"description": "Show outputs", "risk": "read"},
            "state": {"description": "State management", "risk": "read"},
            "import": {"description": "Import existing resources", "risk": "write"},
            "taint": {"description": "Mark resource for recreation", "risk": "write"},
        },
        global_flags=[
            {"name": "chdir", "type": "string", "description": "Working directory"},
        ],
        risk_by_subcommand={
            "init": "write", "plan": "read", "validate": "read", "fmt": "write",
            "output": "read", "state": "read", "show": "read",
            "apply": "write", "destroy": "destructive", "import": "write", "taint": "write",
        },
        examples=[
            "terraform init",
            "terraform plan",
            "terraform apply -auto-approve",
        ],
    ),

    "npm": CLIToolSpec(
        name="npm",
        title="npm",
        description="Node.js package manager",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"(\d+\.\d+\.\d+)",
        subcommands={
            "install": {"description": "Install packages", "risk": "write"},
            "ci": {"description": "Clean install from lockfile", "risk": "write"},
            "update": {"description": "Update packages", "risk": "write"},
            "uninstall": {"description": "Remove packages", "risk": "destructive"},
            "list": {"description": "List installed packages", "risk": "read"},
            "outdated": {"description": "Check for outdated packages", "risk": "read"},
            "audit": {"description": "Security audit", "risk": "read"},
            "run": {"description": "Run package scripts", "risk": "write"},
            "publish": {"description": "Publish package", "risk": "network"},
            "pack": {"description": "Create tarball", "risk": "write"},
        },
        global_flags=[
            {"name": "prefix", "type": "string", "description": "Package directory"},
            {"name": "workspace", "type": "string", "description": "Workspace name"},
        ],
        risk_by_subcommand={
            "list": "read", "outdated": "read", "audit": "read", "view": "read",
            "install": "write", "ci": "write", "update": "write", "run": "write",
            "uninstall": "destructive", "publish": "network", "pack": "write",
        },
        examples=[
            "npm install",
            "npm run build",
            "npm audit",
        ],
    ),

    "pip": CLIToolSpec(
        name="pip",
        title="pip",
        description="Python package installer",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"pip (\d+\.\d+\.\d+)",
        subcommands={
            "install": {"description": "Install packages", "risk": "write"},
            "uninstall": {"description": "Remove packages", "risk": "destructive"},
            "list": {"description": "List installed packages", "risk": "read"},
            "show": {"description": "Show package details", "risk": "read"},
            "freeze": {"description": "Output installed packages", "risk": "read"},
            "check": {"description": "Verify dependencies", "risk": "read"},
            "download": {"description": "Download packages", "risk": "network"},
            "wheel": {"description": "Build wheels", "risk": "write"},
            "hash": {"description": "Compute package hashes", "risk": "read"},
            "cache": {"description": "Cache management", "risk": "write"},
        },
        global_flags=[
            {"name": "requirement", "type": "string", "description": "Requirements file"},
            {"name": "target", "type": "string", "description": "Install directory"},
        ],
        risk_by_subcommand={
            "list": "read", "show": "read", "freeze": "read", "check": "read", "hash": "read",
            "install": "write", "wheel": "write", "cache": "write",
            "uninstall": "destructive", "download": "network",
        },
        examples=[
            "pip install -r requirements.txt",
            "pip list --outdated",
            "pip freeze > requirements.txt",
        ],
    ),

    "curl": CLIToolSpec(
        name="curl",
        title="cURL",
        description="Command line tool for transferring data with URLs",
        category=IntegrationCategory.NETWORKING,
        version_flag="--version",
        version_regex=r"curl (\d+\.\d+\.\d+)",
        subcommands={},
        global_flags=[
            {"name": "X", "type": "string", "description": "HTTP method"},
            {"name": "H", "type": "string", "description": "Header"},
            {"name": "d", "type": "string", "description": "POST data"},
            {"name": "u", "type": "string", "description": "User:password for auth"},
            {"name": "o", "type": "string", "description": "Output file"},
            {"name": "s", "type": "boolean", "description": "Silent mode"},
            {"name": "f", "type": "boolean", "description": "Fail silently on HTTP errors"},
            {"name": "L", "type": "boolean", "description": "Follow redirects"},
            {"name": "v", "type": "boolean", "description": "Verbose"},
        ],
        risk_by_subcommand={"curl": "network"},
        examples=[
            'curl -X GET https://api.example.com',
            'curl -X POST -H "Content-Type: application/json" -d \'{"key":"value"}\' https://api.example.com',
        ],
    ),

    "jq": CLIToolSpec(
        name="jq",
        title="jq",
        description="Lightweight JSON processor",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"jq-(\d+\.\d+\.\d+)",
        subcommands={},
        global_flags=[
            {"name": "r", "type": "boolean", "description": "Raw output"},
            {"name": "c", "type": "boolean", "description": "Compact output"},
            {"name": "s", "type": "boolean", "description": "Slurp input"},
            {"name": "f", "type": "string", "description": "Read filter from file"},
        ],
        risk_by_subcommand={"jq": "read"},
        examples=[
            'jq ".items[].name" file.json',
            'echo \'{"a":1}\' | jq ".a"',
        ],
    ),

    "node": CLIToolSpec(
        name="node",
        title="Node.js",
        description="JavaScript runtime",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"v(\d+\.\d+\.\d+)",
        subcommands={},
        global_flags=[
            {"name": "e", "type": "string", "description": "Evaluate script"},
            {"name": "r", "type": "string", "description": "Require module"},
        ],
        risk_by_subcommand={"node": "write"},
        examples=[
            "node script.js",
            "node -e 'console.log(1+1)'",
        ],
    ),

    "python": CLIToolSpec(
        name="python",
        title="Python",
        description="Python interpreter",
        category=IntegrationCategory.DEVTOOLS,
        version_flag="--version",
        version_regex=r"Python (\d+\.\d+\.\d+)",
        subcommands={},
        global_flags=[
            {"name": "c", "type": "string", "description": "Execute command"},
            {"name": "m", "type": "string", "description": "Run module"},
            {"name": "B", "type": "boolean", "description": "Don't write .pyc files"},
        ],
        risk_by_subcommand={"python": "write"},
        examples=[
            "python script.py",
            "python -c 'print(1+1)'",
        ],
    ),
}


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class DetectedCLI:
    """A CLI tool found on the system."""
    name: str
    path: str
    version: str | None
    spec: CLIToolSpec
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "title": self.spec.title,
            "description": self.spec.description,
            "category": self.spec.category.value,
            "subcommands": list(self.spec.subcommands.keys()),
            "examples": self.spec.examples,
        }


@dataclass
class DiscoveryResult:
    """Result of CLI discovery scan."""
    detected: list[DetectedCLI]
    missing: list[str]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": [d.to_dict() for d in self.detected],
            "missing": self.missing,
            "errors": self.errors,
            "total_detected": len(self.detected),
            "total_known": len(KNOWN_CLI_TOOLS),
        }


# =============================================================================
# Discovery Functions
# =============================================================================

def discover_cli_tools(
    names: list[str] | None = None,
    path_dirs: list[str] | None = None,
) -> DiscoveryResult:
    """Scan for known CLI tools on the system PATH.

    Args:
        names: Specific tool names to check (None = all known)
        path_dirs: Additional directories to search

    Returns:
        DiscoveryResult with detected tools and missing ones
    """
    detected = []
    missing = []
    errors = []

    target_names = names or list(KNOWN_CLI_TOOLS.keys())

    for name in target_names:
        if name not in KNOWN_CLI_TOOLS:
            errors.append(f"Unknown CLI tool: {name}")
            continue

        spec = KNOWN_CLI_TOOLS[name]
        try:
            result = _detect_single_cli(spec, path_dirs)
            if result:
                detected.append(result)
            else:
                missing.append(name)
        except Exception as e:
            errors.append(f"Error detecting {name}: {e}")
            missing.append(name)

    return DiscoveryResult(detected=detected, missing=missing, errors=errors)


def _detect_single_cli(
    spec: CLIToolSpec,
    path_dirs: list[str] | None = None,
) -> DetectedCLI | None:
    """Detect a single CLI tool."""
    # Search in PATH + additional dirs
    search_dirs = list(path_dirs or [])
    search_dirs.extend(os.environ.get("PATH", "").split(os.pathsep))

    for d in search_dirs:
        if not d:
            continue
        candidate = Path(d) / spec.name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            # Found executable, get version
            version = _get_cli_version(str(candidate), spec)
            return DetectedCLI(
                name=spec.name,
                path=str(candidate),
                version=version,
                spec=spec,
                available=True,
            )

    return None


def _get_cli_version(path: str, spec: CLIToolSpec) -> str | None:
    """Get version of a CLI tool."""
    try:
        result = subprocess.run(
            [path, *shlex.split(spec.version_flag)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout + result.stderr
        match = re.search(spec.version_regex, output)
        if match:
            return match.group(1)
        return output.strip().split("\n")[0] if output.strip() else None
    except Exception:
        return None


def get_cli_help(name: str, subcommand: str | None = None) -> str | None:
    """Get help text for a CLI tool or subcommand."""
    spec = KNOWN_CLI_TOOLS.get(name)
    if not spec:
        return None

    try:
        path = shutil.which(name)
        if not path:
            return None

        args = [path]
        if subcommand:
            args.extend(shlex.split(subcommand))
        args.extend(["--help"])

        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return result.stdout or result.stderr
    except Exception:
        return None


def parse_cli_help(name: str, help_text: str) -> dict[str, Any]:
    """Parse CLI help text to extract subcommands and options."""
    # This is a basic parser - can be enhanced
    lines = help_text.split("\n")
    result = {"subcommands": {}, "global_options": []}

    in_subcommands = False
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect subcommand section
        if line.lower().startswith(("commands:", "subcommands:", "available commands:")):
            in_subcommands = True
            continue

        if in_subcommands and line.startswith(("  ", "\t")):
            # Parse subcommand line
            parts = line.split()
            if parts:
                cmd = parts[0]
                desc = " ".join(parts[1:]) if len(parts) > 1 else ""
                if cmd not in result["subcommands"]:
                    result["subcommands"][cmd] = {"description": desc, "options": []}

    return result


# =============================================================================
# Integration Skeleton Generator
# =============================================================================

def generate_cli_integration(
    cli_name: str,
    output_dir: str | Path | None = None,
    custom_subcommands: list[str] | None = None,
    include_all_subcommands: bool = False,
) -> dict[str, Any]:
    """Generate a SkeletonKey integration skeleton for a CLI tool.

    Args:
        cli_name: Name of the CLI tool (must be in KNOWN_CLI_TOOLS)
        output_dir: Directory to write integration file (None = return code only)
        custom_subcommands: Specific subcommands to include
        include_all_subcommands: Include all known subcommands

    Returns:
        Dict with generated code and metadata
    """
    spec = KNOWN_CLI_TOOLS.get(cli_name)
    if not spec:
        return {"error": f"Unknown CLI tool: {cli_name}"}

    # Determine which subcommands to include
    if custom_subcommands:
        subcommands = {k: v for k, v in spec.subcommands.items() if k in custom_subcommands}
    elif include_all_subcommands:
        subcommands = spec.subcommands
    else:
        # Default: include common safe subcommands
        common = {"status", "list", "get", "describe", "logs", "show", "version", "help"}
        subcommands = {k: v for k, v in spec.subcommands.items() if k in common}
        if not subcommands and spec.subcommands:
            # Fallback: first 5 subcommands
            subcommands = dict(list(spec.subcommands.items())[:5])

    # Generate integration code
    code = _generate_integration_code(cli_name, spec, subcommands)

    # Write to file if requested
    if output_dir:
        output_path = Path(output_dir) / f"{cli_name}_integration.py"
        output_path.write_text(code, encoding="utf-8")
        return {
            "cli_name": cli_name,
            "output_path": str(output_path),
            "subcommands_included": list(subcommands.keys()),
            "code_length": len(code),
        }

    return {
        "cli_name": cli_name,
        "code": code,
        "subcommands_included": list(subcommands.keys()),
    }


def _generate_integration_code(
    cli_name: str,
    spec: CLIToolSpec,
    subcommands: dict[str, dict[str, Any]],
) -> str:
    """Generate the integration Python code."""
    integration_name = cli_name.replace("-", "_")
    class_name = "".join(w.capitalize() for w in cli_name.split("-"))

    lines = [
        f'"""Auto-generated SkeletonKey integration for {spec.title} CLI."""',
        "",
        "from __future__ import annotations",
        "",
        "import shlex",
        "import shutil",
        "import subprocess",
        "from typing import Any",
        "",
        "from skeletonkey.core.errors import E, SkeletonKeyError",
        "from skeletonkey.integrations import (",
        "    Integration,",
        "    IntegrationConfig,",
        "    IntegrationType,",
        "    IntegrationCategory,",
        "    IntegrationPlatform,",
        "    IntegrationCapability,",
        "    IntegrationDependency,",
        "    register_integration,",
        ")",
        "",
        f'@register_integration(',
        f'    name="{cli_name}",',
        f'    title="{spec.title}",',
        f'    version="1.0.0",',
        f'    integration_type=IntegrationType.CLI,',
        f'    category=IntegrationCategory.{spec.category.name},',
        f'    platform=IntegrationPlatform.CROSS_PLATFORM,',
        f'    description="{spec.description}",',
        f'    author="SkeletonKey Auto-Generator",',
        f'    license="MIT",',
        f'    keywords={json.dumps([cli_name, spec.category.value.lower()])},',
        f'    dependencies=[',
        f'        IntegrationDependency(name="{cli_name}", optional=False, kind="binary")',
        f'    ],',
        f'    capabilities=[',
        f'        IntegrationCapability(name="shell", description="Requires shell access", required=True),',
        f'    ],',
        f')',
        f'class {class_name}Integration(Integration):',
        f'    """Auto-generated wrapper for {spec.title} CLI."""',
        f'',
        f'    def __init__(self, config: IntegrationConfig):',
        f'        super().__init__(config)',
        f'        self._cli_path = config.config.get("cli_path") or shutil.which("{cli_name}")',
        f'        if not self._cli_path:',
        f'            raise SkeletonKeyError(',
        f'                E.BAD_ARGS,',
        f'                f"{cli_name} not found in PATH",',
        f'                details={{"cli": "{cli_name}"}}',
        f'            )',
        f'',
        f'    def initialize(self) -> bool:',
        f'        # Verify CLI is accessible',
        f'        try:',
        f'            subprocess.run([self._cli_path, "{spec.version_flag}"],',
        f'                           capture_output=True, timeout=5, check=False)',
        f'            self._initialized = True',
        f'            return True',
        f'        except Exception:',
        f'            return False',
        f'',
        f'    def cleanup(self) -> None:',
        f'        self._initialized = False',
        f'',
        f'    def check_health(self) -> tuple[str, str]:',
        f'        if not self._initialized:',
        f'            return ("unhealthy", "CLI not initialized")',
        f'        return ("healthy", "{spec.title} CLI is available")',
        f'',
        f'    def get_tools(self) -> list[dict[str, Any]]:',
        f'        tools = []',
    ]

    # Generate tool definitions for each subcommand
    for subcmd, info in subcommands.items():
        risk = spec.risk_by_subcommand.get(subcmd, "write")
        if risk == "*":
            risk = "write"

        tool_id = cli_name + "." + subcmd.replace("-", "_")
        capability = cli_name + "." + subcmd.replace("-", "_")

        # Build input schema based on common patterns
        input_schema = _generate_subcommand_schema(subcmd, info)

        tags_list = [cli_name, subcmd, spec.category.value.lower()]
        tags = json.dumps(tags_list)
        default_desc = info.get("description", "Run " + cli_name + " " + subcmd)
        lines.extend((
            '        tools.append({',
            '            "id": "' + tool_id + '",',
            '            "title": "' + spec.title + ' ' + subcmd + '",',
            '            "description": ' + json.dumps(default_desc) + ',',
            '            "capability": "' + capability + '",',
            '            "risk": "' + risk + '",',
            '            "tier": "task",',
            '            "tags": ' + tags + ',',
            '            "input_schema": ' + json.dumps(input_schema) + ',',
            '        })'
        ))

    lines.append('        return tools')
    lines.append('')

    # Generate handler methods
    for subcmd in subcommands:
        method_name = "tool_" + subcmd.replace("-", "_").replace(".", "_")
        lines.extend([
            '    def ' + method_name + '(self, args: list[str] = None, **kwargs) -> dict[str, Any]:',
            '        """Run ' + cli_name + ' ' + subcmd + '."""',
            '        cmd_args = [self._cli_path, "' + subcmd + '"]',
            '        if args:',
            '            cmd_args.extend(args)',
            '        # Handle kwargs as flags',
            '        for k, v in kwargs.items():',
            '            if isinstance(v, bool) and v:',
            '                cmd_args.append("--" + k)',
            '            elif v is not None:',
            '                cmd_args.append("--" + k)',
            '                cmd_args.append(str(v))',
            '        try:',
            '            result = subprocess.run(',
            '                cmd_args,',
            '                capture_output=True,',
            '                text=True,',
            '                timeout=self.config.config.get("timeout", 60),',
            '                check=False,',
            '            )',
            '            return {',
            '                "exit_code": result.returncode,',
            '                "stdout": result.stdout,',
            '                "stderr": result.stderr,',
            '                "success": result.returncode == 0,',
            '            }',
            '        except subprocess.TimeoutExpired as e:',
            '            raise SkeletonKeyError(E.TIMEOUT, "' + cli_name + ' ' + subcmd + ' timed out") from e',
            '        except Exception as e:',
            '            raise SkeletonKeyError(E.INTERNAL, "' + cli_name + ' ' + subcmd + ' failed: " + str(e)) from e',
            '',
        ])

    # Add generic run tool for arbitrary commands
    lines.extend([
        '    def tool_run(self, args: list[str]) -> dict[str, Any]:',
        '        """Run arbitrary ' + cli_name + ' command."""',
        '        cmd_args = [self._cli_path]',
        '        cmd_args.extend(args)',
        '        try:',
        '            result = subprocess.run(',
        '                cmd_args,',
        '                capture_output=True,',
        '                text=True,',
        '                timeout=self.config.config.get("timeout", 60),',
        '                check=False,',
        '            )',
        '            return {',
        '                "exit_code": result.returncode,',
        '                "stdout": result.stdout,',
        '                "stderr": result.stderr,',
        '                "success": result.returncode == 0,',
        '            }',
        '        except subprocess.TimeoutExpired as e:',
        '            raise SkeletonKeyError(E.TIMEOUT, "' + cli_name + ' command timed out") from e',
        '        except Exception as e:',
        '            raise SkeletonKeyError(E.INTERNAL, "' + cli_name + ' command failed: " + str(e)) from e',
    ])

    return "\n".join(lines)


def _generate_subcommand_schema(subcmd: str, info: dict[str, Any]) -> dict[str, Any]:
    """Generate JSON schema for a subcommand."""
    # Basic schema with args array
    schema = {
        "type": "object",
        "properties": {
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Arguments for {subcmd}",
            },
        },
        "additionalProperties": True,  # Allow arbitrary flags
    }

    # Add common flags based on subcommand
    if subcmd in ("clone", "init", "build", "create"):
        schema["properties"]["path"] = {"type": "string", "description": "Path/directory"}
    if subcmd in ("run", "exec"):
        schema["properties"]["command"] = {"type": "array", "items": {"type": "string"}}

    return schema


# =============================================================================
# Batch Generation
# =============================================================================

def generate_all_cli_integrations(
    output_dir: str | Path,
    only_detected: bool = True,
) -> dict[str, Any]:
    """Generate integrations for all detected CLI tools."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if only_detected:
        result = discover_cli_tools()
        cli_names = [d.name for d in result.detected]
    else:
        cli_names = list(KNOWN_CLI_TOOLS.keys())

    results = {"generated": [], "failed": []}

    for name in cli_names:
        try:
            gen_result = generate_cli_integration(name, output_path)
            if "error" not in gen_result:
                results["generated"].append(gen_result)
            else:
                results["failed"].append({"name": name, "error": gen_result["error"]})
        except Exception as e:
            results["failed"].append({"name": name, "error": str(e)})

    return results


# =============================================================================
# CLI Interface
# =============================================================================

def discover_clis_action(
    names: list[str] | None = None,
    json_output: bool = False,
) -> dict[str, Any]:
    """Action for `sk integrations discover-clis`."""
    result = discover_cli_tools(names)
    return result.to_dict()


def generate_cli_integration_action(
    cli_name: str,
    output_dir: str | None = None,
    subcommands: list[str] | None = None,
    all_subcommands: bool = False,
) -> dict[str, Any]:
    """Action for `sk integrations generate-cli`."""
    return generate_cli_integration(
        cli_name=cli_name,
        output_dir=output_dir,
        custom_subcommands=subcommands,
        include_all_subcommands=all_subcommands,
    )