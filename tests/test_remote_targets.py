"""Tests for the P7 remote target probe module (skeletonkey.remote)."""

from __future__ import annotations

import pytest

from skeletonkey.remote.targets import (
    ProbeReport,
    ProbeResult,
    TargetSpec,
    _parse_results,
    _run_local,
    probe_local,
    probe_target,
)

# ---- TargetSpec tests ------------------------------------------------------

def test_target_spec_local():
    spec = TargetSpec(kind="local", name="test")
    d = spec.to_dict()
    assert d["kind"] == "local"
    assert d["name"] == "test"


def test_target_spec_docker():
    spec = TargetSpec(kind="docker", container="my-container", name="docker:my-container")
    d = spec.to_dict()
    assert d["kind"] == "docker"
    assert d["container"] == "my-container"


def test_target_spec_ssh():
    spec = TargetSpec(kind="ssh", host="example.com", user="deploy", port=2222)
    d = spec.to_dict()
    assert d["kind"] == "ssh"
    assert d["host"] == "example.com"
    assert d["user"] == "deploy"
    assert d["port"] == 2222


def test_target_spec_ssh_default_port_omitted():
    spec = TargetSpec(kind="ssh", host="example.com")
    d = spec.to_dict()
    assert "port" not in d  # default 22 is omitted


# ---- ProbeResult tests -----------------------------------------------------

def test_probe_result_to_dict():
    r = ProbeResult(command="echo hello", ok=True, stdout="hello\n", duration_ms=5)
    d = r.to_dict()
    assert d["command"] == "echo hello"
    assert d["ok"] is True
    assert d["duration_ms"] == 5


# ---- _run_local tests ------------------------------------------------------

def test_run_local_echo():
    r = _run_local("echo hello", timeout=5.0)
    assert r.ok is True
    assert "hello" in r.stdout


def test_run_local_failure():
    r = _run_local("false", timeout=5.0)
    assert r.ok is False


def test_run_local_timeout():
    r = _run_local("sleep 30", timeout=0.1)
    assert r.ok is False
    assert "timed out" in r.stderr


def test_run_local_captures_stderr():
    r = _run_local("echo error >&2", timeout=5.0)
    assert "error" in r.stderr


# ---- _parse_results tests --------------------------------------------------

def test_parse_results_extracts_hostname():
    results = [
        ProbeResult(command="hostname", ok=True, stdout="my-host\n"),
    ]
    info = _parse_results(results)
    assert info["hostname"] == "my-host"


def test_parse_results_extracts_user():
    results = [
        ProbeResult(command="whoami", ok=True, stdout="testuser\n"),
    ]
    info = _parse_results(results)
    assert info["user"] == "testuser"


def test_parse_results_handles_empty():
    info = _parse_results([])
    assert info["os_name"] == ""
    assert info["hostname"] == ""


def test_parse_results_skips_failed_commands():
    results = [
        ProbeResult(command="hostname", ok=False, stderr="command not found"),
    ]
    info = _parse_results(results)
    assert info["hostname"] == ""


# ---- probe_local integration test ------------------------------------------

def test_probe_local_returns_report():
    report = probe_local(timeout_s=5.0)
    assert isinstance(report, ProbeReport)
    assert report.target.kind == "local"
    assert report.duration_ms > 0
    assert len(report.results) > 0


def test_probe_local_finds_os_info():
    report = probe_local(timeout_s=5.0)
    # At minimum we should detect the OS
    assert report.os_name != "" or any(r.ok for r in report.results)


def test_probe_local_finds_python():
    report = probe_local(timeout_s=5.0)
    assert report.python_version != ""


def test_probe_local_report_to_dict():
    report = probe_local(timeout_s=5.0)
    d = report.to_dict()
    assert "target" in d
    assert "os" in d
    assert "probe_count" in d
    assert d["probe_count"] > 0


# ---- probe_target preflight tests ------------------------------------------

def test_probe_target_docker_without_docker_cli():
    """If docker CLI is not found, report an error."""
    import shutil
    has_docker = shutil.which("docker") is not None
    if has_docker:
        pytest.skip("docker CLI is installed; cannot test the missing-docker path")

    spec = TargetSpec(kind="docker", container="nonexistent")
    report = probe_target(spec)
    assert report.error == "docker CLI not found"


def test_probe_target_ssh_without_host():
    """SSH target requires a host."""
    spec = TargetSpec(kind="ssh", host="")
    report = probe_target(spec)
    assert "host" in report.error.lower()


# ---- ProbeReport serialization ---------------------------------------------

def test_probe_report_to_dict_includes_error():
    report = ProbeReport(error="something went wrong")
    d = report.to_dict()
    assert d["error"] == "something went wrong"


def test_probe_report_to_dict_omits_error_when_empty():
    report = ProbeReport()
    d = report.to_dict()
    assert "error" not in d
