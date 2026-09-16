"""Function calling + strict JSON schema control (zero-dependency).

- ``validate``: JSON-Schema subset validator (type/properties/required/
  items/enum/range/pattern/additionalProperties/min-max lengths). When
  the ``jsonschema`` package is installed it is preferred (lazy); the
  builtin keeps the core dependency-free (ADR-0004 tradition).
- ``parse_tool_calls``: extract tool calls from a model's text output
  (```json fences or bare JSON).
- ``run_tool_loop``: the execute→feedback loop — model proposes a tool
  call, we run the registered function, feed the result back, repeat
  until a plain answer or ``max_iterations``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Sequence

from features.common.errors import ValidationError
from features.common.openai_compat import ChatMessage, FunctionCall, Tool, ToolCall

__all__ = [
    "validate",
    "validate_strict",
    "extract_json_block",
    "parse_tool_calls",
    "run_tool_loop",
]

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


# --------------------------------------------------------------------------
# JSON-Schema subset validator
# --------------------------------------------------------------------------

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate ``instance`` against a schema subset; return error list."""
    if not isinstance(schema, dict):
        return []
    errors: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPE_CHECKS.get(x, lambda v: True)(instance) for x in types):
            errors.append(f"{path}: expected type {t}, got {type(instance).__name__}")
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property {req!r}")
        addl = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in props:
                errors.extend(validate(value, props[key], f"{path}.{key}"))
            elif addl is False:
                errors.append(f"{path}: additional property {key!r} not allowed")
            elif isinstance(addl, dict):
                errors.extend(validate(value, addl, f"{path}.{key}"))
    return errors


def validate_strict(instance: Any, schema: dict[str, Any]) -> None:
    """Raise ValidationError with all problems when invalid."""
    errors = validate(instance, schema)
    if errors:
        raise ValidationError("schema violation", detail=errors)


def _prefer_jsonschema() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("jsonschema") is not None
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Tool-call parsing
# --------------------------------------------------------------------------


def extract_json_block(text: str) -> Any | None:
    """First JSON object in the text: ```json fence first, then bare {}."""
    for m in _CODE_FENCE_RE.finditer(text):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    # bare object: find first balanced {...}
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def parse_tool_calls(text: str, tools: Sequence[Tool] | None = None) -> list[ToolCall]:
    """Extract tool calls from model output.

    Accepts: {"name":..., "arguments":...} | {"function":{...}} |
    {"tool_calls":[...]} shapes, inside code fences or bare JSON.
    """
    data = extract_json_block(text)
    if data is None:
        return []
    raw_calls: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if isinstance(data.get("tool_calls"), list):
            raw_calls = [c for c in data["tool_calls"] if isinstance(c, dict)]
        elif "function" in data:
            raw_calls = [data]
        elif "name" in data:
            raw_calls = [{"function": {"name": data.get("name"),
                                       "arguments": data.get("arguments", {})}}]
    calls: list[ToolCall] = []
    known = {t.function.get("name") for t in tools} if tools else None
    for i, raw in enumerate(raw_calls):
        fn = raw.get("function") if isinstance(raw.get("function"), dict) else raw
        name = fn.get("name", "")
        if known is not None and name and name not in known:
            continue
        args = fn.get("arguments", {})
        if not isinstance(args, str):
            args = json.dumps(args)
        calls.append(ToolCall(
            id=raw.get("id") or f"call_{i}_{name}",
            type=raw.get("type", "function"),
            function=FunctionCall(name=name, arguments=args)))
    return calls


# --------------------------------------------------------------------------
# Tool loop
# --------------------------------------------------------------------------

ToolExecutor = Callable[[dict[str, Any]], Any] | Callable[[dict[str, Any]], Awaitable[Any]]


async def run_tool_loop(
    engine: Any,
    model: str | None,
    messages: list[ChatMessage],
    tools: list[Tool] | None,
    executors: dict[str, ToolExecutor] | None,
    max_iterations: int = 5,
    trace: list[dict[str, Any]] | None = None,
) -> tuple[str, list[ToolCall]]:
    """Drive the model until it answers without (new) tool calls.

    Returns (final_text, all_tool_calls). Executor errors are fed back to
    the model as tool results (it can retry/correct).
    """
    from features.inference_engine.backends.base import GenerationParams

    executors = executors or {}
    all_calls: list[ToolCall] = []
    history = list(messages)
    for iteration in range(max_iterations):
        text = await engine.generate(model, history, GenerationParams(max_tokens=1024))
        calls = parse_tool_calls(text, tools)
        if not calls:
            return text, all_calls
        for call in calls:
            all_calls.append(call)
            if trace is not None:
                trace.append({"iteration": iteration, "call": call.to_dict()})
            history.append(ChatMessage(role="assistant", content=text,
                                       tool_calls=[call]))
            executor = executors.get(call.name)
            if executor is None:
                result = json.dumps({"error": f"unknown tool {call.name!r}"})
            else:
                try:
                    out = executor(call.args)
                    if hasattr(out, "__await__"):
                        out = await out
                    result = out if isinstance(out, str) else json.dumps(out, default=str)
                except Exception as exc:  # noqa: BLE001 - feedback loop by design
                    result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
            history.append(ChatMessage(role="tool", tool_call_id=call.id,
                                       content=result))
    # exhausted: return last model text (may still contain a tool call)
    return text, all_calls
