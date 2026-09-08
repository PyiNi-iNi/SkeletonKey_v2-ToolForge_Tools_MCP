"""End-to-end local pack lifecycle coverage."""

from __future__ import annotations

import json

from skeletonkey.integrations.packs import IntegrationPackManager


def _write_manifest(path, version):
    (path / "pack.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "name": "demo-pack",
                "version": version,
                "title": "Demo Pack",
                "description": "A local upgrade fixture",
                "integrations": [],
                "metadata": {"migration": version},
            }
        ),
        encoding="utf-8",
    )


def test_local_pack_upgrade_replaces_version_and_persists_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _write_manifest(source, "1.0.0")
    manager = IntegrationPackManager(install_root=tmp_path / "installed")

    assert manager.install(str(source), "local")
    assert manager.get_installed("demo-pack").version == "1.0.0"

    _write_manifest(source, "2.0.0")
    assert manager.upgrade("demo-pack")
    upgraded = manager.get_installed("demo-pack")
    assert upgraded is not None
    assert upgraded.version == "2.0.0"
    assert upgraded.metadata["migration"] == "2.0.0"

    reloaded = IntegrationPackManager(install_root=tmp_path / "installed")
    persisted = reloaded.get_installed("demo-pack")
    assert persisted is not None and persisted.version == "2.0.0"
