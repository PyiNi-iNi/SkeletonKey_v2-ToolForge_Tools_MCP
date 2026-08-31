"""Small shared utilities: ids, stable JSON, hashing, token estimation.

Kept dependency-free on purpose (ADR-0001): the core must be importable in a
bare interpreter inside an agent sandbox.
"""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import re
import uuid
from typing import Any

# --------------------------------------------------------------------------- ids


def new_run_id() -> str:
    """Sortable, collision-resistant id: ULID-ish (time prefix + entropy)."""
    import time

    ms = int(time.time() * 1000)
    return f"{ms:013x}{uuid.uuid4().hex[:10]}"


def short_hash(data: bytes | str, length: int = 12) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogateescape")
    return hashlib.sha256(data).hexdigest()[:length]


def content_hash(obj: Any) -> str:
    """Stable hash over a JSON-able object (key order independent)."""
    return "sha256:" + hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_fallback)


def compact_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=False, separators=(",", ":"), ensure_ascii=False, default=_fallback)


def pretty_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=False, indent=2, ensure_ascii=False, default=_fallback)


def _fallback(obj: Any) -> Any:
    for attr in ("to_json", "model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                continue
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return str(obj)


# ------------------------------------------------------------------- byte/text


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def to_bytes(value: str | bytes) -> bytes:
    return value.encode("utf-8", "surrogateescape") if isinstance(value, str) else value


def clip(text: str, limit: int, *, marker: str = "\n...[clipped {n} chars]...\n") -> str:
    """Deterministic middle-out truncation: keep head 65% / tail 35%."""
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.65)
    tail = limit - head
    dropped = len(text) - limit
    return text[:head] + marker.format(n=dropped) + text[-tail:]


# ----------------------------------------------------------------------- tokens

# Rough heuristic used for budgeting only - never for billing. Intentionally
# conservative and deterministic so replayed runs compute the same numbers.
_TOKEN_RE = re.compile(r"[A-Za-z]+|[0-9]+|.", re.UNICODE)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # ~4 chars/token for prose, but identifiers/JSON tokenize worse; use a
    # word+symbol count scaled to a char floor, whichever is larger.
    words = len(_TOKEN_RE.findall(text))
    by_chars = len(text) // 4
    return max(1, words, by_chars)


def fit_token_budget(text: str, max_tokens: int) -> str:
    approx_chars = max(0, max_tokens * 4)
    return clip(text, approx_chars)


# ------------------------------------------------------------------ platform


def is_windows() -> bool:
    return platform.system() == "Windows"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


ANSI_RE = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[BPK_]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


# ---------------------------------------------------------------------- globs
# Shared by the policy engine (core/policy.py) and the engine's gate/deny
# matching: one implementation, so a rule's `**` cannot mean different things
# in the two places that refuse a call.

_GLOB_CACHE: dict[str, re.Pattern[str]] = {}


def glob_to_re(glob: str) -> re.Pattern[str]:
    """fnmatch-ish with `**` spanning separators; `**/...` also matches the tail."""
    out: list[str] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i:i + 2] == "**":
                if glob[i + 2:i + 3] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile(r"^(?:" + "".join(out) + r")(?:/.*)?$")


def glob_hit(glob: str, candidate: str) -> bool:
    rx = _GLOB_CACHE.get(glob)
    if rx is None:
        rx = glob_to_re(glob)
        _GLOB_CACHE[glob] = rx
    return bool(rx.search(candidate))


def env_fingerprint(env: dict[str, str] | None = None) -> str:
    """Hash of PATH + key discriminators; used to invalidate probes/caches."""
    import os

    source = env if env is not None else dict(os.environ)
    interesting = {
        "PATH": source.get("PATH", ""),
        "SystemRoot": source.get("SystemRoot", ""),
        "COMSPEC": source.get("COMSPEC", ""),
        "SKELETONKEY_PROFILE": source.get("SKELETONKEY_PROFILE", ""),
    }
    return short_hash(compact_json({"p": interesting, "s": platform.system(), "m": platform.machine()}), 16)
