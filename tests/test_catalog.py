"""Catalog system Phase 3 tests: publish, package, watch, diff, merge, generators."""

from __future__ import annotations

import json
import tempfile
import os

import pytest

from skeletonkey.core.config import Config
from skeletonkey.toolkit import build


def call(engine, tool, /, **args):
    """Positional-only `tool`: some tools take an argument literally named `tool`."""
    return engine.call(tool, args)


@pytest.fixture
def catalog_toolkit(workspace, tmp_path):
    """Toolkit with a fresh catalog in a temp directory."""
    catalog_path = tmp_path / "catalog.json"
    cfg = Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)],
        "state": {"dir": str(workspace / ".sk")},
        "shell": {"tempdir": str(workspace / ".sk" / "shell")},
        "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                   "confirm_destructive": False},
        "log_level": "ERROR",
    })
    tk = build(config=cfg)
    # Create catalog
    r = call(tk.engine, "catalog.create", auto_index=True, save=False)
    assert r.ok
    yield tk
    tk.close()


class TestCatalogPublish:
    """Tests for catalog.publish tool."""

    def test_publish_requires_registry_url(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.publish")
        assert not r.ok
        assert "registry_url" in r.error.message.lower()

    def test_publish_succeeds_with_valid_url(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.publish",
                 registry_url="https://example.com/registry")
        assert r.ok
        assert r.data["published"] is True
        assert r.data["registry_url"] == "https://example.com/registry"
        assert "tool_count" in r.data

    def test_publish_accepts_auth_token(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.publish",
                 registry_url="https://example.com/registry",
                 auth_token="test-token-123")
        assert r.ok
        assert r.data["published"] is True

    def test_publish_returns_tool_count(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.publish",
                 registry_url="https://example.com/registry")
        assert r.ok
        assert isinstance(r.data["tool_count"], int)
        assert r.data["tool_count"] > 0


class TestCatalogPackage:
    """Tests for catalog.package tool."""

    def test_package_creates_json_by_default(self, catalog_toolkit, tmp_path):
        r = call(catalog_toolkit.engine, "catalog.package")
        assert r.ok
        assert r.data["packaged"] is True
        assert r.data["format"] == "json"
        assert "path" in r.data
        # Verify file exists and is valid JSON
        pkg_path = r.data["path"]
        assert os.path.exists(pkg_path)
        with open(pkg_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "schema_version" in data
        assert "tools" in data
        assert "generated_at" in data

    def test_package_accepts_custom_path(self, catalog_toolkit, tmp_path):
        custom_path = str(tmp_path / "my-catalog.json")
        r = call(catalog_toolkit.engine, "catalog.package", path=custom_path)
        assert r.ok
        assert r.data["path"] == custom_path
        assert os.path.exists(custom_path)

    def test_package_only_supports_json_format(self, catalog_toolkit):
        # JSONL not supported
        r = call(catalog_toolkit.engine, "catalog.package", format="jsonl")
        assert not r.ok
        assert "jsonl" in str(r.error).lower() or "format" in str(r.error).lower()

        # CSV not supported
        r = call(catalog_toolkit.engine, "catalog.package", format="csv")
        assert not r.ok
        assert "csv" in str(r.error).lower() or "format" in str(r.error).lower()


class TestCatalogWatch:
    """Tests for catalog.watch tool."""

    def test_watch_runs_single_iteration(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.watch", max_iterations=1)
        assert r.ok
        assert r.data["watched"] is True
        assert r.data["iterations"] == 1
        assert r.data["stopped"] is True

    def test_watch_accepts_interval_and_debounce(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.watch",
                 interval=0.1, debounce=0.05, max_iterations=1)
        assert r.ok
        assert r.data["iterations"] == 1

    def test_watch_stops_after_max_iterations(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.watch", max_iterations=3)
        assert r.ok
        assert r.data["iterations"] == 3
        assert r.data["stopped"] is True


class TestCatalogGenerateAudit:
    """Tests for catalog.generate_audit tool (requires jsonschema)."""

    def test_generate_audit_requires_jsonschema(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.generate_audit")
        # May fail due to missing jsonschema dependency
        if not r.ok:
            assert "jsonschema" in str(r.error).lower() or "module" in str(r.error).lower()


class TestCatalogDiff:
    """Tests for enhanced catalog.diff tool."""

    def test_diff_compare_with_self(self, catalog_toolkit, tmp_path):
        # Save current catalog
        r1 = call(catalog_toolkit.engine, "catalog.save", path=str(tmp_path / "cat1.json"))
        assert r1.ok
        # Diff with itself (use same file for both old and new)
        r = call(catalog_toolkit.engine, "catalog.diff",
                 old_path=str(tmp_path / "cat1.json"), new_path=str(tmp_path / "cat1.json"), ignore_timestamps=True)
        assert r.ok
        assert "added" in r.data
        assert "removed" in r.data
        assert "modified" in r.data
        # Should have no differences (same catalog)
        assert r.data["added"] == []
        assert r.data["removed"] == []

    def test_diff_patch_format(self, catalog_toolkit, tmp_path):
        r1 = call(catalog_toolkit.engine, "catalog.save", path=str(tmp_path / "cat1.json"))
        assert r1.ok
        r = call(catalog_toolkit.engine, "catalog.diff",
                 old_path=str(tmp_path / "cat1.json"), new_path=str(tmp_path / "cat1.json"), output_format="patch")
        assert r.ok
        assert "added" in r.data

    def test_diff_json_format(self, catalog_toolkit, tmp_path):
        r1 = call(catalog_toolkit.engine, "catalog.save", path=str(tmp_path / "cat1.json"))
        assert r1.ok
        r = call(catalog_toolkit.engine, "catalog.diff",
                 old_path=str(tmp_path / "cat1.json"), new_path=str(tmp_path / "cat1.json"), output_format="detailed")
        assert r.ok
        assert "added" in r.data
        assert "removed" in r.data
        assert "modified" in r.data


class TestCatalogMerge:
    """Tests for catalog.merge tool."""

    def test_merge_with_self(self, catalog_toolkit, tmp_path):
        r1 = call(catalog_toolkit.engine, "catalog.save", path=str(tmp_path / "cat1.json"))
        assert r1.ok
        r = call(catalog_toolkit.engine, "catalog.merge",
                 base_path=str(tmp_path / "cat1.json"), overlay_path=str(tmp_path / "cat1.json"), strategy="union")
        assert r.ok
        assert "merged" in r.data
        assert r.data["merged"] is True

    def test_merge_strategy_union(self, catalog_toolkit, tmp_path):
        r1 = call(catalog_toolkit.engine, "catalog.save", path=str(tmp_path / "cat1.json"))
        assert r1.ok
        r = call(catalog_toolkit.engine, "catalog.merge",
                 base_path=str(tmp_path / "cat1.json"), overlay_path=str(tmp_path / "cat1.json"), strategy="union")
        assert r.ok

    def test_merge_strategy_replace(self, catalog_toolkit, tmp_path):
        r1 = call(catalog_toolkit.engine, "catalog.save", path=str(tmp_path / "cat1.json"))
        assert r1.ok
        r = call(catalog_toolkit.engine, "catalog.merge",
                 base_path=str(tmp_path / "cat1.json"), overlay_path=str(tmp_path / "cat1.json"), strategy="replace")
        assert r.ok

    def test_merge_strategy_keep_existing(self, catalog_toolkit, tmp_path):
        r1 = call(catalog_toolkit.engine, "catalog.save", path=str(tmp_path / "cat1.json"))
        assert r1.ok
        r = call(catalog_toolkit.engine, "catalog.merge",
                 base_path=str(tmp_path / "cat1.json"), overlay_path=str(tmp_path / "cat1.json"), strategy="overlay")
        assert r.ok


class TestCatalogDistribution:
    """Tests for catalog.get_distribution tool."""

    def test_distribution_basic(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.get_distribution")
        assert r.ok
        # Data is returned directly, not wrapped in "distribution"
        assert "total" in r.data
        assert "by_risk" in r.data
        assert "by_capability" in r.data
        assert "by_tag" in r.data

    def test_distribution_includes_counts(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.get_distribution")
        assert r.ok
        dist = r.data  # Data is returned directly
        # Total should match catalog stats
        total_by_risk = sum(dist["by_risk"].values())
        total_by_capability = sum(dist["by_capability"].values())
        assert total_by_risk > 0
        assert total_by_capability > 0
        assert total_by_risk == total_by_capability


class TestCatalogGenerators:
    """Tests for catalog generator tools."""

    def test_generate_schema(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.generate_schema")
        assert r.ok
        assert "generated" in r.data
        assert r.data["generated"] is True
        assert "schema" in r.data
        assert r.data["format"] == "json"

    def test_generate_inventory(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.generate_inventory")
        assert r.ok
        assert "generated" in r.data
        assert r.data["generated"] is True
        assert "format" in r.data

    def test_generate_audit_may_fail(self, catalog_toolkit):
        r = call(catalog_toolkit.engine, "catalog.generate_audit")
        # May fail due to missing jsonschema dependency
        if not r.ok:
            assert "jsonschema" in str(r.error).lower() or "module" in str(r.error).lower()


class TestCatalogCLI:
    """Tests for CLI integration of new Phase 3 commands."""

    def test_cli_catalog_create(self, workspace, tmp_path):
        cfg = Config.load(cwd=str(workspace), overrides={
            "roots": [str(workspace)],
            "state": {"dir": str(workspace / ".sk")},
            "shell": {"tempdir": str(workspace / ".sk" / "shell")},
            "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                       "confirm_destructive": False},
            "log_level": "ERROR",
        })
        from skeletonkey.cli import main
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            main(["catalog", "create"])
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "Catalog created" in output
        assert "tools" in output

    def test_cli_catalog_package(self, workspace, tmp_path):
        cfg = Config.load(cwd=str(workspace), overrides={
            "roots": [str(workspace)],
            "state": {"dir": str(workspace / ".sk")},
            "shell": {"tempdir": str(workspace / ".sk" / "shell")},
            "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                       "confirm_destructive": False},
            "log_level": "ERROR",
        })
        from skeletonkey.cli import main
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            main(["catalog", "create"])
            main(["catalog", "package"])
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "Package created" in output

    def test_cli_catalog_publish(self, workspace, tmp_path):
        cfg = Config.load(cwd=str(workspace), overrides={
            "roots": [str(workspace)],
            "state": {"dir": str(workspace / ".sk")},
            "shell": {"tempdir": str(workspace / ".sk" / "shell")},
            "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                       "confirm_destructive": False},
            "log_level": "ERROR",
        })
        from skeletonkey.cli import main
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            main(["catalog", "create"])
            main(["catalog", "publish", "https://example.com/registry"])
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "Catalog published" in output

    def test_cli_catalog_watch(self, workspace, tmp_path):
        cfg = Config.load(cwd=str(workspace), overrides={
            "roots": [str(workspace)],
            "state": {"dir": str(workspace / ".sk")},
            "shell": {"tempdir": str(workspace / ".sk" / "shell")},
            "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                       "confirm_destructive": False},
            "log_level": "ERROR",
        })
        from skeletonkey.cli import main
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            main(["catalog", "create"])
            main(["catalog", "watch", "--max-iterations", "1"])
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "Watch stopped" in output
        assert "1 iterations" in output