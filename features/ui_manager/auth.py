"""Authentication: native (scrypt + server-side sessions) and OAuth device
grant (self-contained IdP for private deployments).

- Passwords: PBKDF2-free scrypt hash with per-user 16-byte salt
  (``hashlib.scrypt`` — stdlib, cross-platform, no deps).
- Sessions: opaque 32-byte tokens, server-side, TTL-bound, revocable.
- Device flow: RFC 8628-shaped state machine (device_code / user_code /
  polling) so OAuth-compatible clients can log in from headless hosts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from features.common.errors import AuthError, ValidationError

__all__ = ["hash_password", "verify_password", "NativeAuth", "DeviceFlow"]

_SCrypt_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCrypt_N, r=_SCRYPT_R,
                       p=_SCRYPT_P, dklen=32)
    return h.hex(), salt.hex()


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    try:
        candidate, _ = hash_password(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(candidate, hash_hex)
    except (ValueError, TypeError):
        return False


@dataclass
class _User:
    username: str
    password_hash: str
    salt: str
    created: float
    failed_attempts: int = 0
    locked_until: float = 0.0


class NativeAuth:
    """User store + session manager. Optional NDJSON persistence file."""

    def __init__(self, users_file: str | None = None, session_ttl: int = 3600,
                 max_attempts: int = 5, lockout_seconds: int = 300,
                 clock: Callable[[], float] | None = None):
        self._clock = clock or time.time
        self.session_ttl = session_ttl
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self.users_file = users_file
        self._users: dict[str, _User] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        if users_file and os.path.exists(users_file):
            self._load(users_file)

    # -- users ---------------------------------------------------------------

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("action") == "user":
                    u = rec["user"]
                    self._users[u["username"]] = _User(
                        username=u["username"], password_hash=u["password_hash"],
                        salt=u["salt"], created=u["created"])

    def _persist_user(self, user: _User) -> None:
        if not self.users_file:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.users_file)), exist_ok=True)
        with open(self.users_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": self._clock(), "action": "user",
                "user": {"username": user.username, "password_hash": user.password_hash,
                         "salt": user.salt, "created": user.created},
            }) + "\n")

    def add_user(self, username: str, password: str) -> None:
        if not username or not password:
            raise ValidationError("username and password required")
        if username in self._users:
            raise ValidationError(f"user {username!r} already exists")
        h, s = hash_password(password)
        self._users[username] = _User(username, h, s, self._clock())
        self._persist_user(self._users[username])

    def user_exists(self, username: str) -> bool:
        return username in self._users

    # -- sessions ------------------------------------------------------------

    def login(self, username: str, password: str) -> str:
        now = self._clock()
        user = self._users.get(username)
        if user is None:
            raise AuthError("invalid credentials")
        if user.locked_until > now:
            raise AuthError("account locked",
                            hint=f"retry in {int(user.locked_until - now)}s")
        if not verify_password(password, user.password_hash, user.salt):
            user.failed_attempts += 1
            if user.failed_attempts >= self.max_attempts:
                user.locked_until = now + self.lockout_seconds
                user.failed_attempts = 0
            raise AuthError("invalid credentials")
        user.failed_attempts = 0
        token = secrets.token_hex(32)
        self._sessions[token] = {"username": username, "created": now,
                                 "expires": now + self.session_ttl}
        return token

    def issue_session(self, username: str) -> str:
        """Mint a session without password check (device-flow approval)."""
        if username not in self._users:
            raise ValidationError(f"unknown user {username!r}")
        token = secrets.token_hex(32)
        now = self._clock()
        self._sessions[token] = {"username": username, "created": now,
                                 "expires": now + self.session_ttl}
        return token

    def logout(self, token: str) -> None:
        self._sessions.pop(token, None)

    def whoami(self, token: str) -> str | None:
        sess = self._sessions.get(token)
        if sess is None:
            return None
        if sess["expires"] < self._clock():
            self._sessions.pop(token, None)
            return None
        return sess["username"]

    def active_sessions(self) -> int:
        now = self._clock()
        alive = sum(1 for s in self._sessions.values() if s["expires"] >= now)
        return alive


class DeviceFlow:
    """RFC 8628-shaped device grant, self-hosted for private deployments.

    create_code -> (device_code, user_code); the user approves user_code
    (on any device); polling device_code yields the session token.
    """

    def __init__(self, auth: NativeAuth, clock: Callable[[], float] | None = None,
                 ttl_seconds: int = 600, user_code_alphabet: str = "BCDFGHJMPQRTVWXY"):
        self.auth = auth
        self._clock = clock or (auth._clock if hasattr(auth, "_clock") else time.time)
        self.ttl = ttl_seconds
        self._alphabet = user_code_alphabet
        self._pending: dict[str, dict[str, Any]] = {}

    def _make_user_code(self) -> str:
        rng = secrets.SystemRandom()
        return "".join(rng.choice(self._alphabet) for _ in range(8))

    def create_code(self, username: str) -> tuple[str, str]:
        if not self.auth.user_exists(username):
            raise ValidationError(f"unknown user {username!r}")
        device_code = uuid.uuid4().hex
        user_code = self._make_user_code()
        self._pending[device_code] = {
            "username": username, "user_code": user_code,
            "status": "pending", "created": self._clock(), "expires": None,
        }
        return device_code, user_code

    def approve(self, user_code: str) -> None:
        for entry in self._pending.values():
            if entry["user_code"].upper() == user_code.strip().upper():
                if entry["status"] == "pending":
                    entry["status"] = "approved"
                    entry["token"] = self.auth.issue_session(entry["username"])
                return
        raise ValidationError("unknown user_code")

    def poll(self, device_code: str) -> dict[str, Any]:
        entry = self._pending.get(device_code)
        now = self._clock()
        if entry is None:
            raise ValidationError("unknown device_code")
        if entry["status"] == "pending" and now - entry["created"] > self.ttl:
            entry["status"] = "expired"
        if entry["status"] == "approved":
            self._pending.pop(device_code, None)
            return {"status": "approved", "token": entry["token"],
                    "username": entry["username"]}
        if entry["status"] == "expired":
            return {"status": "expired"}
        return {"status": "pending"}

