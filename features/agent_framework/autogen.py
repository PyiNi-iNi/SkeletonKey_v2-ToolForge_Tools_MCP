"""AutoGen-style multi-agent conversation (local implementation).

Round-robin group chat over the platform's InferenceEngine: each agent
sees the full history, replies, and the conversation ends on a
termination token (default ``APPROVE``) or max rounds. The official
``autogen`` package is NOT required — this is the same pattern with the
same event shape, kept zero-dependency.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from features.common.openai_compat import ChatMessage

__all__ = ["Agent", "AutoGenConversation", "ConversationResult"]


@dataclass
class Message:
    sender: str
    content: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"sender": self.sender, "content": self.content, "ts": self.ts}


@dataclass
class ConversationResult:
    task: str
    messages: list[Message]
    rounds: int
    terminated: bool  # True = hit termination token (not just max rounds)
    termination_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "messages": [m.to_dict() for m in self.messages],
            "rounds": self.rounds,
            "terminated": self.terminated,
            "termination_reason": self.termination_reason,
        }


class Agent:
    """One conversing participant bound to a model on the shared engine."""

    def __init__(self, name: str, engine: Any, model: str | None = None,
                 system: str = "You are a helpful collaborator. Be concise.",
                 is_terminator: bool = False):
        self.name = name
        self.engine = engine
        self.model = model
        self.system = system
        self.is_terminator = is_terminator

    def _history(self, task: str, messages: Sequence[Message]) -> list[ChatMessage]:
        out = [ChatMessage(role="system", content=self.system),
               ChatMessage(role="user", content=f"Task: {task}")]
        for m in messages:
            if m.sender == self.name:
                out.append(ChatMessage(role="assistant", content=m.content))
            else:
                out.append(ChatMessage(role="user", content=f"{m.sender}: {m.content}"))
        return out

    async def reply(self, task: str, messages: Sequence[Message]) -> str:
        from features.inference_engine.backends.base import GenerationParams

        text = await self.engine.generate(
            self.model, self._history(task, messages), GenerationParams(max_tokens=256))
        return text.strip() or f"[{self.name} has no reply]"


class AutoGenConversation:
    def __init__(self, agents: Sequence[Agent], max_rounds: int = 6,
                 termination_token: str = "APPROVE",
                 should_stop: Callable[[Sequence[Message]], bool] | None = None):
        if not agents:
            raise ValueError("at least one agent required")
        self.agents = list(agents)
        self.max_rounds = max_rounds
        self.termination_token = termination_token
        self.should_stop = should_stop

    async def run(self, task: str, first_speaker: int = 0) -> ConversationResult:
        messages: list[Message] = []
        idx = first_speaker % len(self.agents)
        rounds = 0
        terminated = False
        reason = ""
        while rounds < self.max_rounds:
            agent = self.agents[idx]
            reply = await agent.reply(task, messages)
            msg = Message(sender=agent.name, content=reply)
            messages.append(msg)
            rounds += 1
            if self.termination_token and self.termination_token in reply.upper():
                terminated, reason = True, f"token {self.termination_token!r}"
                break
            if self.should_stop and self.should_stop(messages):
                terminated, reason = True, "predicate"
                break
            idx = (idx + 1) % len(self.agents)
        return ConversationResult(task=task, messages=messages, rounds=rounds,
                                  terminated=terminated,
                                  termination_reason=reason or ("max_rounds" if rounds == self.max_rounds else ""))
