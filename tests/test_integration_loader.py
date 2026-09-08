"""P5.3 dynamic loader: file / module / discovery load paths.

The wire tests only exercised the registry ``integrations.load`` path. These
tests cover the rest of the P5.3 contract: ``load_from_path`` (a temp module
file -> class registered -> instance loaded -> engine tools callable ->
unload/reload), ``load_from_module`` (an importable module), the dependency
and conflict gates, and discovery sources (metadata found without the class
is an explicit, non-crashing "class not found"; registering the class
completes the load).
"""

from __future__ import annotations

import sys

import pytest

from skeletonkey.integrations import (
    IntegrationDiscovery,
    IntegrationStatus,
    IntegrationType,
    registry,
)
from skeletonkey.integrations.discovery import FilesystemDiscoverySource
from skeletonkey.integrations.loader import IntegrationLoader

from .integration_helpers import make_temp_integration


@pytest.fixture
def temp_int(tmp_path):
    handle = make_temp_integration(tmp_path)
    yield handle
    handle["cleanup"]()


def test_load_from_path_registers_class_and_loads_instance(temp_int):
    loader = IntegrationLoader(registry=registry)
    result = loader.load_from_path(temp_int["file"])
    assert result.success, result.errors

    name = temp_int["name"]
    assert registry.has_integration(name)
    assert registry.get_integration_class(name) is not None
    assert loader.is_loaded(name)
    assert not loader.is_loading(name)

    inst = loader.get_loaded(name)
    assert inst.status is IntegrationStatus.LOADED
    assert inst.health == "healthy"
    assert inst.info.name == name
    assert loader.get_statistics()["loaded_names"] == [name]


def test_load_from_path_error_paths(tmp_path):
    loader = IntegrationLoader(registry=registry)

    missing = tmp_path / "nope.py"
    result = loader.load_from_path(missing)
    assert not result.success
    assert any("Path does not exist" in e for e in result.errors)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = loader.load_from_path(empty_dir)
    assert not result.success
    assert any("No integration found in directory" in e for e in result.errors)

    junk = tmp_path / "junk.py"
    junk.write_text("VALUE = 42\n", encoding="utf-8")
    result = loader.load_from_path(junk)
    assert not result.success
    assert any("No integration class found" in e for e in result.errors)
    sys.modules.pop("junk", None)


def test_load_from_module_importable(temp_int, tmp_path):
    loader = IntegrationLoader(registry=registry)
    sys.path.insert(0, str(tmp_path))
    try:
        result = loader.load_from_module(temp_int["stem"])
    finally:
        sys.path.remove(str(tmp_path))
    assert result.success, result.errors
    assert loader.is_loaded(temp_int["name"])


def test_dynamic_load_wires_tools_through_the_engine(toolkit, temp_int):
    mgr = toolkit.integrations
    assert mgr is not None
    name = temp_int["name"]
    tool_id = f"{name}.echo"

    # file -> registered class + loaded instance (loader path)
    result = mgr._loader.load_from_path(temp_int["file"])
    assert result.success, result.errors

    # instance -> engine tools (manager path)
    inst = mgr.load_integration_dynamic(name)
    assert inst is not None and mgr.is_loaded_dynamic(name)
    assert toolkit.engine.registry.has(tool_id)

    res = toolkit.engine.call(tool_id, {"text": "ping"})
    assert res.ok, res.error
    assert res.data["echo"] == "ping"

    stats = mgr.get_loader_statistics()
    assert name in stats["loaded_names"]
    assert stats["by_type"].get(IntegrationType.SDK.value, 0) >= 1

    # unload removes the engine tools AND the instance
    assert mgr.unload_integration_dynamic(name)
    assert not toolkit.engine.registry.has(tool_id)
    assert not mgr.is_loaded_dynamic(name)
    assert name not in mgr.get_loader_statistics()["loaded_names"]

    # the class stays registered: loading again makes a fresh, callable instance
    inst2 = mgr.load_integration_dynamic(name)
    assert inst2 is not None and toolkit.engine.registry.has(tool_id)
    res2 = toolkit.engine.call(tool_id, {"text": "pong"})
    assert res2.ok, res2.error
    assert res2.data["echo"] == "pong"
    assert mgr.unload_integration_dynamic(name)


def test_reload_integration_applies_new_config(toolkit, temp_int):
    mgr = toolkit.integrations
    name = temp_int["name"]
    temp_int["register"]()  # class registered, nothing loaded yet

    inst1 = mgr.load_integration_dynamic(name, config={"mode": "one"})
    assert inst1 is not None
    assert inst1.config.config.get("mode") == "one"

    inst2 = mgr.reload_integration(name, config={"mode": "two"})
    assert inst2 is not None
    assert inst2.config.config.get("mode") == "two"
    assert inst2.instance is not inst1.instance  # a fresh instance

    assert mgr.unload_integration_dynamic(name)


def test_load_unknown_integration_reports_not_found(temp_int):
    loader = IntegrationLoader(registry=registry)
    result = loader.load("no-such-integration")
    assert not result.success
    assert any("not found" in e for e in result.errors)
    assert not loader.is_loaded("no-such-integration")


def test_dependency_gate_blocks_load_and_optional_passes(tmp_path):
    loader = IntegrationLoader(registry=registry)

    blocked = make_temp_integration(tmp_path, deps=[("no_such_package_zz9", False)])
    blocked["register"]()
    result = loader.load(blocked["name"])
    assert not result.success
    assert any(
        "Missing required dependency no_such_package_zz9" in e for e in result.errors
    )
    blocked["cleanup"]()

    optional = make_temp_integration(tmp_path, deps=[("no_such_package_zz9", True)])
    optional["register"]()
    result = loader.load(optional["name"])
    assert result.success, result.errors
    # an ABSENT optional dep is ignored (only present deps are recorded)
    assert "no_such_package_zz9" not in result.dependencies_loaded
    loader.unload(optional["name"])
    optional["cleanup"]()


def test_conflicts_are_detected_with_loaded_integrations(tmp_path):
    other = make_temp_integration(tmp_path)
    clashing = make_temp_integration(tmp_path, conflicts=(other["name"],))
    other["register"]()
    clashing["register"]()

    loader = IntegrationLoader(registry=registry)
    try:
        assert loader.load(other["name"]).success
        result = loader.load(clashing["name"])
        assert not result.success
        assert any("conflict" in e.lower() for e in result.errors)
    finally:
        loader.unload(other["name"])
        other["cleanup"]()
        clashing["cleanup"]()


def test_discovery_source_finds_metadata_and_loader_loads_after_register(tmp_path):
    handle = make_temp_integration(tmp_path)  # written, NOT registered
    name = handle["name"]
    try:
        source = FilesystemDiscoverySource(
            name="tmp-src", priority=999, paths=[str(tmp_path)]
        )
        result = source.discover()
        assert [i.name for i in result.integrations] == [name]

        discovery = IntegrationDiscovery()
        for source_name in ("entry_points", "filesystem", "packages", "config",
                            "registry"):
            assert discovery.remove_source(source_name)
        discovery.add_source(source)
        found = discovery.get_integration_info(name)
        assert found is not None and found.name == name

        loader = IntegrationLoader(registry=registry, discovery=discovery)

        # Discovery finds the metadata but not the class: the load must fail
        # with an explicit reason, not a crash.
        result = loader.load(name)
        assert not result.success
        assert any(f"class for {name} not found" in e for e in result.errors)

        # Registering the discovered class completes the contract.
        handle["register"]()
        result = loader.load(name)
        assert result.success, result.errors
        assert loader.is_loaded(name)
    finally:
        loader.unload(name)
        handle["cleanup"]()
