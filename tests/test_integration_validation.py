"""P5.5 Integration testing & validation framework tests.

Two layers, like the rest of the suite:

1. **Validation harness** (import-level, side-effect free): every registered
   integration must survive `validate_integration`, and every ``get_tools()``
   id must map to a callable ``tool_*`` handler (the exact bug class the
   bridge fixes).

2. **Wire-level SDK tests**: the GitHub and Stripe integrations are driven
   against a local in-process stub HTTP server, proving the zero-dependency
   urllib transport, token gating, form encoding, base64 content encoding, and
   the rate/request-id metadata envelopes — without touching the real APIs.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.integrations import IntegrationConfig, registry
from skeletonkey.integrations.bridge import tool_suffix
from skeletonkey.integrations.validation import validate_integration, validate_registry


# --------------------------------------------------------------------------- stub API
class _Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []

    def add(self, method: str, path: str, headers: dict[str, str], body: bytes) -> None:
        with self.lock:
            self.requests.append((method, path, headers, body))


class _Handler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        server = self.server  # type: ignore[attr-defined]
        server.recorder.add(self.command, self.path, dict(self.headers), body)
        payload = json.dumps(server.response).encode("utf-8")
        self.send_response(server.status)
        self.send_header("Content-Type", "application/json")
        for key, value in (server.extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def stub_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.recorder = _Recorder()  # type: ignore[attr-defined]
    server.response = {"object": "list", "data": [{"id": "stub-1"}], "has_more": False}  # type: ignore[attr-defined]
    server.status = 200  # type: ignore[attr-defined]
    server.extra_headers = {}  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _url(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def _instance(name: str, **config):
    cls = registry.get_integration_class(name)
    assert cls is not None, f"integration {name!r} not registered"
    return cls(IntegrationConfig(name=name, integration_id=name, config=config))


# --------------------------------------------------------------------------- validation
def test_registry_inventory_includes_github_and_stripe():
    names = {info.name for info in registry.list_integrations()}
    assert {"github", "stripe"} <= names
    assert len(names) >= 9


def test_zero_dependency_integrations_validate_clean():
    for name in ("github", "stripe", "simple-demo", "sqlite"):
        report = validate_integration(name)
        assert report.valid, report.errors
        assert not report.errors, report.errors


def test_every_registered_tool_maps_to_a_handler():
    """The core bridge invariant: no integration may advertise an orphan tool."""
    for info in registry.list_integrations():
        inst = _instance(info.name)
        for tool in inst.get_tools():
            suffix = tool_suffix(info.name, tool["id"])
            assert suffix is not None, f"{info.name}: unmappable id {tool['id']!r}"
            assert callable(
                getattr(inst, f"tool_{suffix}", None)
            ), f"{info.name}: {tool['id']!r} has no tool_{suffix}()"


def test_validation_flags_missing_required_dependency():
    for name in ("aws-s3", "mongodb", "postgresql", "redis", "requests"):
        info = registry.get_integration_info(name)
        assert info is not None
        required = [d.name for d in info.dependencies if not d.optional]
        report = validate_integration(name)
        if all(importlib.util.find_spec(dep) is None for dep in required):
            codes = {issue.code for issue in report.errors}
            assert "dependencies" in codes, f"{name} should flag its missing deps"
        else:
            # Deps installed: no dependency errors expected.
            assert all(issue.code != "dependencies" for issue in report.errors)


def test_validate_registry_covers_every_integration():
    reports = validate_registry()
    registered = {info.name for info in registry.list_integrations()}
    assert {r.name for r in reports} == registered
    for report in reports:
        assert report.checks["tools"] >= 0


def test_package_reexports_validation_api():
    from skeletonkey.integrations import (
        ValidationIssue,
        benchmark_integration,
    )
    from skeletonkey.integrations import (
        validate_integration as vi,
    )
    from skeletonkey.integrations import (
        validate_registry as vr,
    )
    assert ValidationIssue("code", "warning", "msg").to_dict()["code"] == "code"
    assert vr() and vi("sqlite").valid
    bench = benchmark_integration("github", iterations=50)
    assert bench["name"] == "github" and bench["tool_count"] == 18


def test_bridge_nested_namespace_suffix():
    assert tool_suffix("github", "github.repo.get") == "repo_get"
    assert tool_suffix("stripe", "stripe.payment_intents.create") == "payment_intents_create"
    assert tool_suffix("aws-s3", "aws-s3.list_buckets") == "list_buckets"
    assert tool_suffix("sqlite", "sqlite.query") == "query"
    assert tool_suffix("github", "other.prefix.x") is None


# --------------------------------------------------------------------------- github
def test_github_requires_token_for_writes():
    inst = _instance("github")
    with pytest.raises(SkeletonKeyError) as exc:
        inst.tool_issues_create("owner", "repo", "title")
    assert exc.value.code == "BAD_ARGS"


def test_github_rate_limit_envelope(stub_api):
    stub_api.extra_headers = {  # type: ignore[attr-defined]
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Remaining": "4999",
        "X-RateLimit-Reset": "1700000000",
    }
    inst = _instance("github", base_url=_url(stub_api))
    inst.initialize()
    try:
        result = inst.tool_rate_limit()
    finally:
        inst.cleanup()
    assert result["_rate"] == {"limit": 5000, "remaining": 4999, "reset_epoch": 1700000000}


def test_github_contents_update_base64_encodes_content(stub_api):
    inst = _instance(
        "github", base_url=_url(stub_api), token="ghp_testtoken"
    )
    inst.initialize()
    try:
        result = inst.tool_contents_update(
            "owner", "repo", "path/to/file.py", "add file", "print('hi')\n"
        )
    finally:
        inst.cleanup()

    method, path, _headers, body = stub_api.recorder.requests[0]  # type: ignore[attr-defined]
    assert method == "PUT"
    # GitHub expects content paths percent-encoded; slashes become %2F.
    assert path == "/repos/owner/repo/contents/path%2Fto%2Ffile.py"
    payload = json.loads(body.decode("utf-8"))
    assert payload["content"] == base64.b64encode(b"print('hi')\n").decode("ascii")
    assert result["object"] == "list"  # passthrough payload


def test_github_http_error_surfaces_remote_code(stub_api):
    stub_api.status = 422  # type: ignore[attr-defined]
    stub_api.response = {"message": "Validation Failed", "errors": [{"field": "title"}]}  # type: ignore[attr-defined]
    inst = _instance("github", base_url=_url(stub_api), token="ghp_testtoken")
    inst.initialize()
    try:
        with pytest.raises(SkeletonKeyError) as exc:
            inst.tool_issues_create("owner", "repo", "title")
    finally:
        inst.cleanup()
    assert exc.value.code == "REMOTE"
    assert exc.value.details["status"] == 422


# --------------------------------------------------------------------------- stripe
def test_stripe_requires_key_for_reads():
    inst = _instance("stripe")
    with pytest.raises(SkeletonKeyError) as exc:
        inst.tool_balance()
    assert exc.value.code == "BAD_ARGS"


def test_stripe_form_encodes_post_body_and_surfaces_request_id(stub_api):
    stub_api.extra_headers = {"Request-Id": "req_test_123"}  # type: ignore[attr-defined]
    inst = _instance("stripe", base_url=_url(stub_api), token="sk_test_123")
    inst.initialize()
    try:
        result = inst.tool_customers_create(email="a@b.com", name="Ada")
    finally:
        inst.cleanup()

    method, path, headers, body = stub_api.recorder.requests[0]  # type: ignore[attr-defined]
    assert method == "POST"
    assert path == "/customers"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    decoded = urllib.parse.parse_qs(body.decode("utf-8"))
    assert decoded["email"] == ["a@b.com"]
    assert decoded["name"] == ["Ada"]
    assert result["_request_id"] == "req_test_123"


def test_stripe_error_maps_stripe_code(stub_api):
    stub_api.status = 400  # type: ignore[attr-defined]
    stub_api.response = {  # type: ignore[attr-defined]
        "error": {"type": "invalid_request_error", "code": "parameter_missing", "message": "Missing amount"}
    }
    inst = _instance("stripe", base_url=_url(stub_api), token="sk_test_123")
    inst.initialize()
    try:
        with pytest.raises(SkeletonKeyError) as exc:
            inst.tool_balance()
    finally:
        inst.cleanup()
    assert exc.value.code == "REMOTE"
    assert exc.value.details["stripe_code"] == "parameter_missing"


# --------------------------------------------------------------------------- engine wire
def test_engine_loads_github_and_calls_tool(engine, stub_api):
    url = _url(stub_api)
    stub_api.extra_headers = {"X-RateLimit-Remaining": "12"}  # type: ignore[attr-defined]

    result = engine.call("integrations.load", {"name": "github", "config": {"base_url": url}})
    assert result.ok, result.error
    assert engine.registry.has("github.rate_limit")

    result = engine.call("github.rate_limit", {})
    assert result.ok, result.error
    assert result.data["_rate"]["remaining"] == 12

    engine.call("integrations.unload", {"name": "github"})
    assert not engine.registry.has("github.rate_limit")


def test_engine_integrations_validate_tool(engine):
    result = engine.call("integrations.validate", {})
    assert result.ok, result.error
    assert result.data["count"] >= 9
    assert "valid" in result.data

    result = engine.call("integrations.validate", {"name": "github"})
    assert result.ok and result.data["valid"] is True
