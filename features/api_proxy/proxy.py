"""OpenAIProxy — request handling core for the OpenAI-compatible server.

One class does: model resolution, function-calling loops, JSON-mode
enforcement with retry, and OpenAI-shaped response assembly. The HTTP
layer (server.py) is a thin adapter.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from features.common.errors import FeatureError, NotFoundError, ValidationError
from features.common.openai_compat import (
    ChatCompletionChunk,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    CompletionsRequest,
    CompletionsResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelInfo,
    Usage,
    new_id,
    now_ts,
)
from features.inference_engine.backends.base import GenerationParams

from .function_calling import (
    ToolExecutor,
    extract_json_block,
    run_tool_loop,
    validate,
)

__all__ = ["OpenAIProxy", "estimate_tokens"]


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4) if text else 0


class OpenAIProxy:
    def __init__(self, engine: Any, embed_engine: Any | None = None,
                 tool_executors: dict[str, ToolExecutor] | None = None,
                 json_retry: int = 2, max_tool_iterations: int = 5,
                 owned_by: str = "skeletonkey"):
        self.engine = engine
        self.embed_engine = embed_engine
        self.tool_executors = tool_executors or {}
        self.json_retry = json_retry
        self.max_tool_iterations = max_tool_iterations
        self.owned_by = owned_by
        self._created = now_ts()

    # ------------------------------------------------------------------
    # models / metadata
    # ------------------------------------------------------------------

    def models(self) -> list[ModelInfo]:
        out = [ModelInfo(id=m, created=int(self._created), owned_by=self.owned_by)
               for m in self.engine.model_ids()]
        if self.embed_engine is not None and self.embed_engine.name not in {m.id for m in out}:
            out.append(ModelInfo(id=self.embed_engine.name, created=int(self._created),
                                 owned_by=self.owned_by))
        return out

    def _check_model(self, model: str) -> None:
        if model not in self.engine.model_ids():
            raise NotFoundError(f"The model '{model}' does not exist",
                                hint=f"available: {self.engine.model_ids()}")

    # ------------------------------------------------------------------
    # chat completions
    # ------------------------------------------------------------------

    async def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        req = ChatCompletionRequest.from_dict(body)
        self._check_model(req.model)
        text, tool_calls, _ = await self._complete(req)
        text = await self._enforce_json(req, text)
        prompt_tokens = sum(estimate_tokens(m.content or "") + 1 for m in req.messages)
        usage = Usage.from_counts(prompt_tokens, estimate_tokens(text))
        msg = ChatMessage(role="assistant", content=text)
        if tool_calls:
            msg.tool_calls = tool_calls
        return ChatCompletionResponse(
            id=new_id("chatcmpl"), created=int(self._created), model=req.model,
            choices=[ChatCompletionChoice(
                index=0, message=msg,
                finish_reason="tool_calls" if tool_calls else "stop")],
            usage=usage,
        ).to_dict()

    async def _complete(self, req: ChatCompletionRequest) -> tuple[str, list, list]:
        if req.tools and self.tool_executors:
            trace: list[dict[str, Any]] = []
            text, calls = await run_tool_loop(
                self.engine, req.model, list(req.messages), req.tools,
                self.tool_executors, max_iterations=self.max_tool_iterations, trace=trace)
            return text, calls, trace
        text = await self.engine.generate(req.model, list(req.messages),
                                          GenerationParams.from_request(req))
        return text, [], []

    async def _enforce_json(self, req: ChatCompletionRequest, text: str) -> str:
        if not (req.wants_json_mode or req.wants_json_schema):
            return text
        schema = req.json_schema()
        errors: list[str] = []
        for attempt in range(self.json_retry + 1):
            data = extract_json_block(text)
            if data is not None:
                errors = validate(data, schema) if schema else []
                if not errors:
                    return text
            if attempt < self.json_retry:
                hint = (
                    f"Your last answer was not valid JSON for the schema {json.dumps(schema)}. "
                    "Answer again with JSON only, matching the schema exactly."
                    if schema else
                    "Your last answer was not valid JSON. Answer again with JSON only.")
                feedback = (f"Previous answer:\n{text[:1500]}\n\nErrors: {errors[:5]}\n\n{hint}")
                text = await self.engine.generate(
                    req.model, list(req.messages) + [ChatMessage(role="user", content=feedback)],
                    GenerationParams(max_tokens=1024))
        raise ValidationError(
            "model output is not valid JSON after retries", detail=errors,
            hint="increase max_tokens or relax the schema")

    async def stream_chat(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yield chat.completion.chunk dicts (caller wraps in SSE)."""
        req = ChatCompletionRequest.from_dict(body)
        self._check_model(req.model)
        cid = new_id("chatcmpl")
        if (req.tools and self.tool_executors) or req.wants_json_mode or req.wants_json_schema:
            # full completion first (tools/JSON need the whole text), then
            # re-stream the final text in word-sized deltas
            text, tool_calls, _ = await self._complete(req)
            text = await self._enforce_json(req, text)
            finish = "tool_calls" if tool_calls else "stop"
            for word in text.split(" "):
                delta = ChatMessage(content=word + (" " if word != text.split(" ")[-1] else ""))
                yield ChatCompletionChunk(id=cid, created=int(self._created), model=req.model,
                                          delta=delta).to_dict()
            if tool_calls:
                yield ChatCompletionChunk(id=cid, created=int(self._created), model=req.model,
                                          delta=ChatMessage(role="assistant",
                                                            tool_calls=tool_calls)).to_dict()
            yield ChatCompletionChunk(id=cid, created=int(self._created), model=req.model,
                                      delta=ChatMessage(role="assistant"),
                                      finish_reason=finish).to_dict()
        else:
            gen = self.engine.astream(req.model, list(req.messages),
                                      GenerationParams.from_request(req))
            async for tok in gen:
                yield ChatCompletionChunk(id=cid, created=int(self._created), model=req.model,
                                          delta=ChatMessage(role="assistant", content=tok)).to_dict()
            yield ChatCompletionChunk(id=cid, created=int(self._created), model=req.model,
                                      delta=ChatMessage(role="assistant"),
                                      finish_reason="stop").to_dict()

    # ------------------------------------------------------------------
    # completions
    # ------------------------------------------------------------------

    async def completions(self, body: dict[str, Any]) -> dict[str, Any]:
        req = CompletionsRequest.from_dict(body)
        self._check_model(req.model)
        params = GenerationParams(temperature=req.temperature,
                                  max_tokens=req.max_tokens)
        prompts = [req.prompt] if isinstance(req.prompt, str) else list(req.prompt)
        choices = []
        for i, prompt in enumerate(prompts):
            text = await self.engine.generate(
                req.model, [ChatMessage(role="user", content=prompt)], params)
            choices.append({"text": text, "index": i, "finish_reason": "stop",
                            "logprobs": None})
        usage = Usage.from_counts(sum(estimate_tokens(p) for p in prompts),
                                  sum(estimate_tokens(c["text"]) for c in choices))
        out = {
            "id": new_id("cmpl"), "object": "text_completion",
            "created": int(self._created), "model": req.model,
            "choices": choices, "usage": usage.to_dict(),
        }
        return out

    async def stream_completions(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        req = CompletionsRequest.from_dict(body)
        self._check_model(req.model)
        cid = new_id("cmpl")
        params = GenerationParams(temperature=req.temperature, max_tokens=req.max_tokens)
        prompts = [req.prompt] if isinstance(req.prompt, str) else list(req.prompt)
        for pi, prompt in enumerate(prompts):
            gen = self.engine.astream(req.model, [ChatMessage(role="user", content=prompt)],
                                      params)
            async for tok in gen:
                yield {"id": cid, "object": "text_completion",
                       "created": int(self._created), "model": req.model,
                       "choices": [{"index": pi, "text": tok, "logprobs": None,
                                    "finish_reason": None}]}
            yield {"id": cid, "object": "text_completion",
                   "created": int(self._created), "model": req.model,
                   "choices": [{"index": pi, "text": "", "logprobs": None,
                                "finish_reason": "stop"}]}

    # ------------------------------------------------------------------
    # embeddings
    # ------------------------------------------------------------------

    async def embeddings(self, body: dict[str, Any]) -> dict[str, Any]:
        req = EmbeddingRequest.from_dict(body)
        if self.embed_engine is None:
            raise FeatureError("embeddings are not configured on this proxy",
                               hint="attach a db_manager EmbeddingEngine")
        vectors = await self.embed_engine.aencode(req.inputs)
        usage = Usage.from_counts(sum(estimate_tokens(t) for t in req.inputs), 0)
        return EmbeddingResponse(
            id=new_id("emb"), model=req.model,
            data=[{"object": "embedding", "index": i,
                   "embedding": [round(float(x), 8) for x in v]}
                  for i, v in enumerate(vectors)],
            usage=usage,
        ).to_dict()
