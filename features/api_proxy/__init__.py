"""api_proxy — OpenAI-compatible server proxy.

Serves /v1/chat/completions (streaming SSE + non-streaming),
/v1/completions, /v1/embeddings, /v1/models with the exact OpenAI wire
schemas, plus:

- **function calling**: tools in the request drive an execute→feedback
  loop against registered local functions
- **JSON mode**: ``response_format={"type":"json_object"}`` and strict
  ``json_schema`` enforcement with retry-on-invalid
- **strict schema control**: a zero-dependency JSON-Schema subset
  validator (Outlines-style constrained decoding is the GPU-side
  equivalent; see README)

Backed by the platform's InferenceEngine (any registered model) and
db_manager's EmbeddingEngine for /v1/embeddings.
"""

from __future__ import annotations

from .function_calling import (
    extract_json_block,
    parse_tool_calls,
    run_tool_loop,
    validate,
    validate_strict,
)
from .proxy import OpenAIProxy
from .server import make_proxy_server, run_proxy_server

__all__ = [
    "OpenAIProxy",
    "extract_json_block",
    "make_proxy_server",
    "parse_tool_calls",
    "run_tool_loop",
    "run_proxy_server",
    "validate",
    "validate_strict",
]
