"""Reusable zero-dependency REST integration base.

``RestIntegration`` owns the boring, security-sensitive parts of an HTTP API
integration: URL/query construction, timeouts, JSON decoding, HTTP error
normalisation, and response envelopes. Providers customise only authentication,
request-body encoding, error details, and pagination when their API differs.

The transport is deliberately synchronous and uses ``urllib`` so the core
package keeps its zero mandatory-dependency guarantee. Integration handlers are
called by the engine's worker pool; subclasses must not share mutable request
state between calls.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.integrations import Integration, IntegrationConfig


class RestIntegration(Integration):
    """Base class for integrations backed by a JSON-ish REST API.

    Subclasses normally implement ``get_tools`` and ``tool_*`` methods, and
    optionally override :meth:`_auth_headers`, :meth:`_encode_body`, and
    :meth:`_remote_error`. ``_request`` returns a stable envelope; ``_api`` is
    the convenient raising variant used by tool handlers.
    """

    default_base_url = ""
    user_agent = "skeletonkey-toolforge"

    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
        self._base_url = str(config.config.get("base_url") or self.default_base_url).rstrip("/")
        self._timeout = float(config.config.get("timeout", 30))
        self._default_headers = {
            str(k): str(v) for k, v in (config.config.get("headers") or {}).items()
        }

    def initialize(self) -> bool:
        self._initialized = True
        return True

    def cleanup(self) -> None:
        self._initialized = False

    def _auth_headers(self) -> dict[str, str]:
        """Return provider authentication headers for one request."""
        return {}

    def _encode_body(self, method: str, body: Any) -> tuple[bytes | None, dict[str, str]]:
        """Encode a request body; providers may return form encoding instead."""
        if body is None or method.upper() in {"GET", "HEAD"}:
            return None, {}
        return json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"}

    def _remote_error(self, response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Map an error response to a message and safe details."""
        data = response.get("data")
        message = data.get("message") if isinstance(data, dict) else None
        return (str(message or data or "remote request failed"), {"body": data})

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        params: Any = None,
    ) -> dict[str, Any]:
        """Make a request and return ``status``, ``headers`` and decoded data."""
        url = path if path.startswith(("http://", "https://")) else self._base_url + path
        clean = {k: v for k, v in (query or {}).items() if v is not None}
        if clean:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
        method = method.upper()
        if body is None and params is not None:
            body = params
        data, body_headers = self._encode_body(method, body)
        headers = {"User-Agent": self.user_agent, **self._default_headers, **self._auth_headers(), **body_headers}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                raw = response.read()
                status = int(response.status)
                response_headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = int(exc.code)
            response_headers = {k.lower(): v for k, v in exc.headers.items()}
        except urllib.error.URLError as exc:
            raise SkeletonKeyError(E.REMOTE, f"{self.name} API unreachable: {exc.reason}", details={"path": path}) from exc
        except TimeoutError as exc:
            raise SkeletonKeyError(E.TIMEOUT, f"{self.name} API timed out after {self._timeout}s", details={"timeout_s": self._timeout, "path": path}) from exc
        try:
            parsed: Any = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            parsed = raw.decode("utf-8", errors="replace")
        return {"status": status, "headers": response_headers, "data": parsed}

    def _api(self, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None, params: Any = None) -> dict[str, Any]:
        response = self._request(method, path, query=query, body=body, params=params)
        if response["status"] >= 400:
            message, details = self._remote_error(response)
            raise SkeletonKeyError(E.REMOTE, f"{self.name} API {path} -> HTTP {response['status']}: {message}", details={"status": response["status"], **details})
        return response

    def _envelope(self, response: dict[str, Any]) -> Any:
        """Return the upstream payload; override to attach provider metadata."""
        return response["data"]

    def _paged(self, response: dict[str, Any], *, items_key: str = "data") -> dict[str, Any]:
        """Normalise common list payloads without imposing provider semantics."""
        data = response["data"]
        items = data.get(items_key, []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        result = {"items": items, "count": len(items)}
        if isinstance(data, dict):
            if "has_more" in data:
                result["has_more"] = bool(data["has_more"])
            if "next" in data:
                result["next"] = data["next"]
        return result

    def check_health(self) -> tuple[str, str]:
        return ("healthy", "REST integration is ready") if self._initialized else ("unhealthy", "Integration not initialized")
