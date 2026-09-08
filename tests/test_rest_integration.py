"""Contract tests for the reusable REST integration base."""

from __future__ import annotations

import json

from skeletonkey.integrations import IntegrationConfig, RestIntegration


class DemoREST(RestIntegration):
    default_base_url = "https://example.test/api"

    def __init__(self, config):
        super().__init__(config)
        self.seen = None

    def _auth_headers(self):
        return {"Authorization": "Bearer test"}

    def get_tools(self):
        return []

    def _remote_error(self, response):
        return (response["data"]["error"], {"provider_code": "demo_error"})


def test_rest_base_builds_json_request_and_decodes(monkeypatch):
    captured = {}

    class Response:
        status = 200
        headers: dict[str, str]

        def __init__(self):
            self.headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode()

    def fake_urlopen(request, timeout):
        captured.update(
            method=request.method,
            url=request.full_url,
            headers=dict(request.header_items()),
            body=request.data,
            timeout=timeout,
        )
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    inst = DemoREST(IntegrationConfig(name="demo", integration_id="demo", config={"timeout": 4}))
    response = inst._api("POST", "/items", query={"page": 2, "skip": None}, body={"x": 1})

    assert response["data"] == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://example.test/api/items?page=2"
    assert captured["timeout"] == 4
    assert captured["body"] == b'{"x": 1}'
    assert captured["headers"]["Authorization"] == "Bearer test"


def test_rest_base_pagination_normalizes_common_payload():
    inst = DemoREST(IntegrationConfig(name="demo", integration_id="demo", config={}))
    result = inst._paged({"data": {"data": [{"id": 1}], "has_more": True}})
    assert result == {"items": [{"id": 1}], "count": 1, "has_more": True}


def test_rest_base_retries_transient_reads_but_not_writes(monkeypatch):
    inst = DemoREST(
        IntegrationConfig(
            name="demo", integration_id="demo", config={"retries": 2, "retry_backoff": 0}
        )
    )
    responses = iter(
        [
            {"status": 503, "headers": {}, "data": {"error": "busy"}},
            {"status": 429, "headers": {}, "data": {"error": "busy"}},
            {"status": 200, "headers": {}, "data": {"ok": True}},
        ]
    )
    calls = []
    monkeypatch.setattr(inst, "_request_once", lambda *args, **kwargs: (calls.append(args[0]) or next(responses)))
    assert inst._request("GET", "/health")["data"] == {"ok": True}
    assert calls == ["GET", "GET", "GET"]

    calls.clear()
    monkeypatch.setattr(
        inst,
        "_request_once",
        lambda *args, **kwargs: (calls.append(args[0]) or {"status": 503, "headers": {}, "data": {}}),
    )
    assert inst._request("POST", "/write")["status"] == 503
    assert calls == ["POST"]
