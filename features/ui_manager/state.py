"""Session state preservation — conversations + UI prefs per user.

Storage follows the toolkit convention: an NDJSON journal (append-only,
replayable) plus a latest-snapshot file for O(1) resume.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Conversation", "StateStore"]


@dataclass
class Conversation:
    id: str
    model: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self.updated = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "model": self.model, "messages": self.messages,
                "created": self.created, "updated": self.updated}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Conversation":
        return cls(id=d["id"], model=d.get("model", "default"),
                   messages=list(d.get("messages", [])),
                   created=d.get("created", 0.0), updated=d.get("updated", 0.0))


class StateStore:
    """Per-user state under ``root/<user>/``: ``state.json`` + ``journal.ndjson``."""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _dir(self, user: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user) or "anon"
        d = os.path.join(self.root, safe)
        os.makedirs(d, exist_ok=True)
        return d

    def save(self, user: str, state: dict[str, Any]) -> str:
        d = self._dir(user)
        snap = os.path.join(d, "state.json")
        journal = os.path.join(d, "journal.ndjson")
        tmp = snap + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, snap)
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "action": "save", "state": state}) + "\n")
        return snap

    def load(self, user: str) -> dict[str, Any] | None:
        snap = os.path.join(self._dir(user), "state.json")
        if not os.path.exists(snap):
            return None
        with open(snap, encoding="utf-8") as fh:
            return json.load(fh)

    def replay(self, user: str) -> list[dict[str, Any]]:
        """All journal entries in order (audit / recovery)."""
        journal = os.path.join(self._dir(user), "journal.ndjson")
        if not os.path.exists(journal):
            return []
        out = []
        with open(journal, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def users(self) -> list[str]:
        return sorted(
            e for e in os.listdir(self.root)
            if os.path.isdir(os.path.join(self.root, e))
        )

    def forget(self, user: str) -> None:
        d = self._dir(user)
        for name in ("state.json", "journal.ndjson", "state.json.tmp"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                os.remove(p)


def new_conversation(model: str = "default") -> Conversation:
    return Conversation(id=uuid.uuid4().hex[:16], model=model)
