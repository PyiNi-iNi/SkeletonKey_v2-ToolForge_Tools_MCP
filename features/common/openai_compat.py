"""OpenAI-compatible wire schemas (pure stdlib, dataclass based).

Every API-facing feature (api_proxy, vision, embeddings) speaks the OpenAI
chat/completions wire format so any OpenAI SDK can point at us. Unknown
request fields are preserved in ``extra`` and echoed back on responses that
support passthrough — we never silently drop what a client sent.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "new_id",
    "now_ts",
    "ChatMessage",
    "FunctionCall",
    "ToolCall",
    "Tool",
    "ChatCompletionRequest",
    "ChatCompletionChoice",
    "ChatCompletionResponse",
    "ChatCompletionChunk",
    "Usage",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "ModelInfo",
    "CompletionsRequest",
    "CompletionsResponse",
    "sse_event",
    "sse_done",
    "parse_sse_stream",
]


def new_id(prefix: str = "cmpl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def now_ts() -> int:
    return int(time.time())


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


@dataclass
class ChatMessage:
    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list["ToolCall"] | None = None
    tool_call_id: str | None = None
    function_call: "FunctionCall | None" = None

    @staticmethod
    def _tool_call_from_dict(raw: dict[str, Any]) -> "ToolCall":
        fn = raw.get("function") or {}
        return ToolCall(
            id=raw.get("id") or new_id("call"),
            type=raw.get("type", "function"),
            function=FunctionCall(name=fn.get("name", ""), arguments=fn.get("arguments", "")),
        )

    @staticmethod
    def _function_call_from_dict(raw: dict[str, Any]) -> "FunctionCall":
        return FunctionCall(name=raw.get("name", ""), arguments=raw.get("arguments", ""))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ChatMessage":
        tool_calls = raw.get("tool_calls")
        function_call = raw.get("function_call")
        return cls(
            role=raw.get("role", "user"),
            content=raw.get("content"),
            name=raw.get("name"),
            tool_calls=[cls._tool_call_from_dict(t) for t in tool_calls] if tool_calls else None,
            tool_call_id=raw.get("tool_call_id"),
            function_call=cls._function_call_from_dict(function_call) if function_call else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            out["content"] = self.content
        if self.name is not None:
            out["name"] = self.name
        if self.tool_calls:
            out["tool_calls"] = [t.to_dict() for t in self.tool_calls]
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        if self.function_call is not None:
            out["function_call"] = self.function_call.to_dict()
        return out


@dataclass
class FunctionCall:
    name: str
    arguments: str = ""  # JSON string, per OpenAI wire format

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}


@dataclass
class ToolCall:
    id: str
    type: str
    function: FunctionCall

    @property
    def name(self) -> str:
        return self.function.name

    @property
    def args(self) -> dict[str, Any]:
        if not self.function.arguments:
            return {}
        try:
            return json.loads(self.function.arguments)
        except json.JSONDecodeError:
            return {}

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "function": self.function.to_dict()}


@dataclass
class Tool:
    type: str
    function: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Tool":
        return cls(type=raw.get("type", "function"), function=raw.get("function") or {})

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "function": self.function}


# --------------------------------------------------------------------------
# Chat completions
# --------------------------------------------------------------------------

_KNOWN_REQUEST_FIELDS = {
    "model",
    "messages",
    "temperature",
    "top_p",
    "n",
    "stream",
    "stop",
    "max_tokens",
    "max_completion_tokens",
    "presence_penalty",
    "frequency_penalty",
    "user",
    "tools",
    "tool_choice",
    "response_format",
    "seed",
    "logprobs",
    "top_logprobs",
    "logit_bias",
    "stream_options",
}


@dataclass
class ChatCompletionRequest:
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    n: int = 1
    stream: bool = False
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    user: str | None = None
    tools: list[Tool] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None
    seed: int | None = None
    stream_options: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_max_tokens(self) -> int | None:
        return self.max_completion_tokens or self.max_tokens

    @property
    def wants_json_mode(self) -> bool:
        rf = self.response_format or {}
        return rf.get("type") == "json_object"

    @property
    def wants_json_schema(self) -> bool:
        rf = self.response_format or {}
        return rf.get("type") == "json_schema"

    def json_schema(self) -> dict[str, Any] | None:
        if not self.wants_json_schema:
            return None
        return (self.response_format or {}).get("json_schema", {}).get("schema")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ChatCompletionRequest":
        try:
            messages = [ChatMessage.from_dict(m) for m in raw.get("messages", [])]
        except TypeError as exc:
            raise ValueError(f"messages must be a list of objects: {exc}") from exc
        if not messages:
            raise ValueError("messages must be non-empty")
        tools = raw.get("tools")
        known = _KNOWN_REQUEST_FIELDS
        extra = {k: v for k, v in raw.items() if k not in known}
        return cls(
            model=raw.get("model", "unknown"),
            messages=messages,
            temperature=raw.get("temperature"),
            top_p=raw.get("top_p"),
            n=int(raw.get("n", 1)),
            stream=bool(raw.get("stream", False)),
            stop=raw.get("stop"),
            max_tokens=raw.get("max_tokens"),
            max_completion_tokens=raw.get("max_completion_tokens"),
            presence_penalty=raw.get("presence_penalty"),
            frequency_penalty=raw.get("frequency_penalty"),
            user=raw.get("user"),
            tools=[Tool.from_dict(t) for t in tools] if tools else None,
            tool_choice=raw.get("tool_choice"),
            response_format=raw.get("response_format"),
            seed=raw.get("seed"),
            stream_options=raw.get("stream_options"),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
        }
        for attr in (
            "temperature",
            "top_p",
            "stop",
            "max_tokens",
            "max_completion_tokens",
            "presence_penalty",
            "frequency_penalty",
            "user",
            "tool_choice",
            "response_format",
            "seed",
            "stream_options",
        ):
            val = getattr(self, attr)
            if val is not None:
                out[attr] = val
        if self.n != 1:
            out["n"] = self.n
        if self.stream:
            out["stream"] = True
        if self.tools:
            out["tools"] = [t.to_dict() for t in self.tools]
        out.update(self.extra)
        return out


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.total_tokens is None:
            self.total_tokens = self.prompt_tokens + self.completion_tokens

    @classmethod
    def from_counts(cls, prompt_tokens: int, completion_tokens: int) -> "Usage":
        return cls(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ChatCompletionChoice:
    index: int
    message: ChatMessage
    finish_reason: str = "stop"
    logprobs: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "message": self.message.to_dict(),
            "finish_reason": self.finish_reason,
            "logprobs": self.logprobs,
        }


@dataclass
class ChatCompletionResponse:
    id: str
    model: str
    choices: list[ChatCompletionChoice]
    created: int = field(default_factory=now_ts)
    usage: Usage | None = None
    object: str = "chat.completion"
    system_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": [c.to_dict() for c in self.choices],
        }
        if self.usage is not None:
            out["usage"] = self.usage.to_dict()
        if self.system_fingerprint is not None:
            out["system_fingerprint"] = self.system_fingerprint
        return out


@dataclass
class ChatCompletionChunk:
    id: str
    model: str
    delta: ChatMessage
    index: int = 0
    finish_reason: str | None = None
    created: int = field(default_factory=now_ts)
    usage: Usage | None = None
    object: str = "chat.completion.chunk"

    def to_dict(self) -> dict[str, Any]:
        choice: dict[str, Any] = {
            "index": self.index,
            "delta": self.delta.to_dict(),
        }
        if self.finish_reason is not None:
            choice["finish_reason"] = self.finish_reason
        out: dict[str, Any] = {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": [choice],
        }
        if self.usage is not None:
            out["usage"] = self.usage.to_dict()
        return out


# --------------------------------------------------------------------------
# Text completions (legacy /v1/completions)
# --------------------------------------------------------------------------


@dataclass
class CompletionsRequest:
    model: str
    prompt: str | list[str]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    user: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CompletionsRequest":
        prompt = raw.get("prompt", "")
        if not isinstance(prompt, (str, list)):
            raise ValueError("prompt must be a string or list of strings")
        return cls(
            model=raw.get("model", "unknown"),
            prompt=prompt,
            temperature=raw.get("temperature"),
            max_tokens=raw.get("max_tokens"),
            stream=bool(raw.get("stream", False)),
            user=raw.get("user"),
            extra={k: v for k, v in raw.items() if k not in
                   {"model", "prompt", "temperature", "max_tokens", "stream", "user"}},
        )


@dataclass
class CompletionsResponse:
    id: str
    model: str
    text: str
    index: int = 0
    created: int = field(default_factory=now_ts)
    usage: Usage | None = None
    object: str = "text_completion"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": [{"text": self.text, "index": self.index, "finish_reason": "stop"}],
        }
        if self.usage is not None:
            out["usage"] = self.usage.to_dict()
        return out


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------


@dataclass
class EmbeddingRequest:
    model: str
    input: str | list[str]
    encoding_format: str = "float"
    dimensions: int | None = None
    user: str | None = None

    @property
    def inputs(self) -> list[str]:
        if isinstance(self.input, str):
            return [self.input]
        return [str(x) for x in self.input]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EmbeddingRequest":
        inp = raw.get("input", "")
        if not isinstance(inp, (str, list)):
            raise ValueError("input must be a string or list of strings")
        return cls(
            model=raw.get("model", "text-embedding-ada-002"),
            input=inp,
            encoding_format=raw.get("encoding_format", "float"),
            dimensions=raw.get("dimensions"),
            user=raw.get("user"),
        )


@dataclass
class EmbeddingResponse:
    id: str
    model: str
    data: list[dict[str, Any]]
    usage: Usage
    object: str = "list"

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": self.object,
            "data": self.data,
            "model": self.model,
            "usage": self.usage.to_dict(),
        }


@dataclass
class ModelInfo:
    id: str
    object: str = "model"
    created: int = field(default_factory=now_ts)
    owned_by: str = "skeletonkey"
    root: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "object": self.object, "created": self.created,
                               "owned_by": self.owned_by}
        if self.root is not None:
            out["root"] = self.root
        return out


# --------------------------------------------------------------------------
# SSE helpers
# --------------------------------------------------------------------------


def sse_event(payload: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n\n"


def sse_done() -> bytes:
    return b"data: [DONE]\n\n"


def parse_sse_stream(raw: bytes) -> list[dict[str, Any]]:
    """Parse an SSE byte body into a list of JSON payloads (no [DONE])."""
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        out.append(json.loads(data))
    return out
