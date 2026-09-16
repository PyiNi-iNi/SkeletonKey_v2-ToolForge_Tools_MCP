"""Shared support package for the features platform (not itself a feature).

- ``openai_compat``: OpenAI wire schemas + SSE helpers
- ``streaming``: async stream utilities, 80 tok/s timing
- ``config``: env-driven typed configuration
- ``errors``: unified error taxonomy
- ``http``: stdlib-only async HTTP server with chunked streaming
"""

from __future__ import annotations

from .config import PREFIX, FeatureConfig, env_bool, env_float, env_int, env_list, env_str
from .errors import (
    AuthError,
    BackendUnavailableError,
    ConfigError,
    ExecutionError,
    FeatureError,
    NotFoundError,
    StreamingError,
    UpstreamError,
    ValidationError,
    from_error,
)
from .http import Handler, HttpServer, Request, Response, error_response, json_response
from .openai_compat import (
    ChatCompletionChunk,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    CompletionsRequest,
    CompletionsResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    FunctionCall,
    ModelInfo,
    Tool,
    ToolCall,
    Usage,
    new_id,
    now_ts,
    parse_sse_stream,
    sse_done,
    sse_event,
)
from .streaming import (
    TARGET_TOKENS_PER_SEC,
    TokenClock,
    TimingReport,
    chunk_bytes,
    collect_stream,
    limit_stream,
    merge_stream_errors,
    stream_with_timing,
)

__all__ = [
    "PREFIX",
    "FeatureConfig",
    "env_bool",
    "env_float",
    "env_int",
    "env_list",
    "env_str",
    "AuthError",
    "BackendUnavailableError",
    "ConfigError",
    "ExecutionError",
    "FeatureError",
    "NotFoundError",
    "StreamingError",
    "UpstreamError",
    "ValidationError",
    "from_error",
    "Handler",
    "HttpServer",
    "Request",
    "Response",
    "error_response",
    "json_response",
    "ChatCompletionChunk",
    "ChatCompletionChoice",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatMessage",
    "CompletionsRequest",
    "CompletionsResponse",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "FunctionCall",
    "ModelInfo",
    "Tool",
    "ToolCall",
    "Usage",
    "new_id",
    "now_ts",
    "parse_sse_stream",
    "sse_done",
    "sse_event",
    "TARGET_TOKENS_PER_SEC",
    "TokenClock",
    "TimingReport",
    "chunk_bytes",
    "collect_stream",
    "limit_stream",
    "merge_stream_errors",
    "stream_with_timing",
]
