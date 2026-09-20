"""Small, dependency-free HTTP exposure policy for local preview servers.

The live panel executes Python, so a bind address is a trust decision rather
than a cosmetic option.  This module keeps that decision shared by the panel
and the read-only visualizer:

* loopback is the safe default;
* non-loopback binds require an explicit opt-in and a high-entropy token;
* browser origins are allowlisted when an origin is supplied;
* the advertised URL is never guessed from ``0.0.0.0`` as ``127.0.0.1``.

This is intentionally not a full authentication framework.  It is a fail-closed
boundary for the small development HTTP servers; production deployments should
still put them behind an authenticated, TLS-terminating proxy.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

MIN_TOKEN_BYTES = 32
_COOKIE_NAME = "sk_access"


def is_loopback_bind(host: str) -> bool:
    """Return whether *host* names only the local machine.

    Hostname resolution is deliberately not performed: resolving an arbitrary
    name during server construction would make the bind policy network- and
    DNS-dependent.  Operators should use a literal loopback address for the
    local mode.
    """
    value = (host or "").strip().lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _host_for_url(host: str) -> str:
    value = (host or "").strip()
    if not value:
        return "0.0.0.0"
    if ":" in value and not value.startswith("["):
        return f"[{value}]"
    return value


def bind_url(host: str, port: int) -> str:
    """URL describing the actual socket bind, not necessarily a browser URL."""
    return f"http://{_host_for_url(host)}:{int(port)}/"


def _base_parts(url: str):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("public_base_url must be an absolute http:// or https:// URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("public_base_url must not contain credentials, query, or fragment")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("public_base_url contains an invalid port") from exc
    return parts


def _origin(url: str) -> str:
    parts = _base_parts(url)
    return f"{parts.scheme}://{parts.netloc}"


def advertised_url(host: str, port: int, public_base_url: str = "") -> str | None:
    """Return a URL a browser may use, or ``None`` when it cannot be inferred.

    A wildcard bind is a socket instruction, not an origin.  Replacing it with
    loopback was actively misleading in remote preview environments.
    """
    if public_base_url:
        parts = _base_parts(public_base_url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
        return base.rstrip("/") + "/"
    if is_loopback_bind(host):
        shown = "127.0.0.1" if host in ("", "localhost") else host
        return bind_url(shown, port)
    return None


def _valid_token(token: str | None) -> bool:
    if not token or any(ord(ch) < 0x21 or ord(ch) > 0x7e or ch in ";," for ch in token):
        return False
    try:
        return len(token.encode("utf-8")) >= MIN_TOKEN_BYTES
    except UnicodeError:
        return False


def generate_token() -> str:
    """Generate a token suitable for an operator-provided preview secret."""
    return secrets.token_urlsafe(32)


def _cookie_token(raw: str | None) -> str | None:
    if not raw:
        return None
    for item in raw.split(";"):
        key, sep, value = item.strip().partition("=")
        if sep and key == _COOKIE_NAME:
            return value
    return None


def _query_token(raw_path: str) -> str | None:
    try:
        values = parse_qs(urlsplit(raw_path).query).get("token") or []
        return values[0] if values else None
    except ValueError:
        return None


@dataclass(frozen=True)
class HttpAccessPolicy:
    """Fail-closed request policy shared by preview servers."""

    host: str
    allow_remote: bool = False
    auth_token: str = ""
    allowed_origins: tuple[str, ...] = ()
    public_base_url: str = ""

    def __post_init__(self) -> None:
        bind_remote = not is_loopback_bind(self.host)
        public_remote = False
        if self.public_base_url:
            public_remote = not is_loopback_bind(_base_parts(self.public_base_url).hostname or "")
        if (bind_remote or public_remote) and not self.allow_remote:
            what = "non-loopback HTTP bind" if bind_remote else "a remote public_base_url"
            raise ValueError(
                f"refusing {what}; set allow_remote=true only for an isolated preview "
                "and provide auth_token"
            )
        if (bind_remote or public_remote) and not _valid_token(self.auth_token):
            raise ValueError(
                "remote preview requires an auth_token of at least "
                f"{MIN_TOKEN_BYTES} UTF-8 bytes"
            )
        if self.auth_token and not _valid_token(self.auth_token):
            raise ValueError(f"auth_token must be at least {MIN_TOKEN_BYTES} UTF-8 bytes")
        for origin in self.allowed_origins:
            if origin == "*":
                raise ValueError("allowed_origins must contain exact origins, not '*'")
            if _origin(origin) != origin:
                raise ValueError(
                    f"allowed origin {origin!r} must be an exact scheme://host[:port] origin"
                )
        if self.public_base_url:
            base_origin = _origin(self.public_base_url)
            configured = tuple(self.allowed_origins)
            if configured and base_origin not in configured:
                raise ValueError("public_base_url origin must be present in allowed_origins")

    @property
    def bind_remote(self) -> bool:
        return not is_loopback_bind(self.host)

    @property
    def public_remote(self) -> bool:
        if not self.public_base_url:
            return False
        return not is_loopback_bind(_base_parts(self.public_base_url).hostname or "")

    @property
    def remote(self) -> bool:
        """Whether this socket or its advertised proxy surface is remote."""
        return self.bind_remote or self.public_remote

    @property
    def requires_auth(self) -> bool:
        return bool(self.auth_token) or self.remote

    def _token_ok(self, headers: Any, raw_path: str) -> bool:
        if not self.requires_auth:
            return True
        authorization = str(headers.get("Authorization") or "")
        candidate = ""
        if authorization.lower().startswith("bearer "):
            candidate = authorization[7:].strip()
        candidate = candidate or str(headers.get("X-SkeletonKey-Token") or "")
        candidate = candidate or _cookie_token(headers.get("Cookie"))
        # Query tokens exist only to bootstrap a browser page/EventSource.  The
        # response sets a SameSite cookie, and access logs must be configured to
        # redact this query parameter by the outer proxy.
        candidate = candidate or _query_token(raw_path) or ""
        return bool(candidate) and secrets.compare_digest(candidate, self.auth_token)

    def _same_origin_request(self, origin: str, headers: Any) -> bool:
        """Permit the page served by this socket without making CORS open.

        The authentication requirement still applies to remote binds.  Comparing
        against Host is appropriate here because this is an HTTP origin check,
        not a DNS trust decision; a remote deployment should set
        ``public_base_url``/``allowed_origins`` to make the intended origin
        explicit as well.
        """
        parts = urlsplit(origin)
        host = str(headers.get("Host") or "").strip()
        return parts.scheme == "http" and bool(host) and parts.netloc.lower() == host.lower()

    def origin_allowed(self, headers: Any) -> bool:
        origin = str(headers.get("Origin") or "").strip()
        if not origin:
            return True
        allowed = set(self.allowed_origins)
        if self.public_base_url:
            allowed.add(_origin(self.public_base_url))
        return origin in allowed or self._same_origin_request(origin, headers)

    def check(self, headers: Any, raw_path: str) -> tuple[int, str] | None:
        """Return ``(status, message)`` on refusal, otherwise ``None``."""
        if self.requires_auth and not self._token_ok(headers, raw_path):
            return 401, "preview authentication required"
        if not self.origin_allowed(headers):
            return 403, "request origin is not allowlisted"
        return None

    def token_from_request(self, headers: Any, raw_path: str) -> str | None:
        """Return the validated token so a server can set a bootstrap cookie."""
        if not self.requires_auth:
            return None
        authorization = str(headers.get("Authorization") or "")
        candidate = ""
        if authorization.lower().startswith("bearer "):
            candidate = authorization[7:].strip()
        candidate = candidate or str(headers.get("X-SkeletonKey-Token") or "")
        candidate = candidate or _cookie_token(headers.get("Cookie"))
        candidate = candidate or _query_token(raw_path)
        if candidate and secrets.compare_digest(candidate, self.auth_token):
            return candidate
        return None


def bootstrap_cookie(token: str | None, *, secure: bool = False) -> str | None:
    """Return a restrictive cookie for a successfully authenticated request."""
    if not token:
        return None
    flags = [f"{_COOKIE_NAME}={token}", "Path=/", "HttpOnly", "SameSite=Strict"]
    if secure:
        flags.append("Secure")
    return "; ".join(flags)


def auth_bootstrap_script() -> str:
    """JS for pages bootstrapped with a token in the URL.

    The first remote request uses ``?token`` because fragments never reach
    HTTP. The response sets a restrictive cookie; this script adds an
    Authorization header for page fetches and then removes both query and
    fragment credentials from the address bar. The server remains the
    authority and validates the token on every request.
    """
    return """<script>
(function () {
  const hash = new URLSearchParams((location.hash || '').slice(1));
  const query = new URLSearchParams(location.search || '');
  // A fragment is useful only after an authenticated page has loaded; the
  // first remote request must use ?token because fragments never reach HTTP.
  const token = query.get('token') || hash.get('token');
  if (!token) return;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const opts = Object.assign({}, init || {});
    const headers = new Headers(opts.headers || {});
    headers.set('Authorization', 'Bearer ' + token);
    opts.headers = headers;
    return nativeFetch(input, opts);
  };
  // The authenticated page response sets a SameSite cookie, which is sent
  // with EventSource without putting the token into another URL.
  const clean = new URL(location.href);
  clean.searchParams.delete('token');
  clean.hash = '';
  history.replaceState(null, document.title, clean.pathname + clean.search);
})();
</script>"""


def inject_auth_bootstrap(page: str) -> str:
    """Insert the token bootstrap before page scripts execute."""
    marker = "</head>"
    if marker not in page:
        return page
    return page.replace(marker, auth_bootstrap_script() + marker, 1)


def token_fingerprint(token: str | None) -> str | None:
    """Non-secret diagnostic value for status/logs."""
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "HttpAccessPolicy", "MIN_TOKEN_BYTES", "advertised_url", "auth_bootstrap_script",
    "bind_url", "bootstrap_cookie", "generate_token", "inject_auth_bootstrap",
    "is_loopback_bind", "token_fingerprint",
]
