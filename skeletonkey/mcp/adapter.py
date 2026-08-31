"""Registry -> MCP server bridge (low-level Server, dynamic tools/list).

Why low-level instead of MCPServer's `@tool` decorator:
  * `tools/list` must be computed per session from the live CapabilityProfile
    (gating + provider de-dup + token budget), not fixed at import time;
  * `inputSchema` comes from ToolManifest, so a python signature refactor can
    never silently change the public contract of a tool;
  * we can raise `notifications/tools/list_changed` when a re-probe changes the
    advertised set (e.g. someone installs `rg`).

Handlers follow mcp 2.x: `async def h(ctx, params) -> ResultModel`.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from ..core.engine import ApprovalRequired, CallContext
from ..core.errors import E, SkeletonKeyError
from ..core.util import compact_json
from ..version import __version__

SERVER_TITLE = "SkeletonKey ToolForge"

INSTRUCTIONS = """Adaptive filesystem + multi-shell toolkit for autonomous agents.

How to use this server well:
- The advertised set is capability-gated for THIS host. If something is missing, no
  backend exists for it here; `registry.search` with include_gated=true shows what is
  gated and why.
- Prefer `fs.patch` over `fs.write` when editing an existing file: it returns a unified
  diff, refuses stale writes (expect_sha), and yields an undo_token.
- `shell.run` executes your script verbatim. `data.completed=false` means the script
  never reached its last line, so treat that run as partial - not as a clean failure.
- Pick `dialect` from `shell.available` rather than assuming bash on Windows or pwsh on CI.
- Failures return error.code, error.hint and often next_actions. Follow the hint; do not
  retry an identical call.
- Paths are sandboxed to declared roots. SANDBOX_VIOLATION is a policy answer, not a
  transient error - do not retry it.
"""


class McpBridge:
    """Holds the adapter state; one per server (and one session context)."""

    def __init__(self, toolkit: Any) -> None:
        self.toolkit = toolkit
        self.engine = toolkit.engine
        self.ctx = CallContext.from_config(toolkit.config, session_id=f"mcp-{os.getpid()}")
        self._name_map: dict[str, str] = {}
        self._digest: str | None = None

    # ------------------------------------------------------------------- naming
    def names(self) -> dict[str, str]:
        """mcp name -> tool id. Dots are legal in MCP names, so we keep them."""
        mapping = {}
        for man in self.engine.registry.all():
            mapping[man.id] = man.id
            mapping[man.mcp_name] = man.id
        self._name_map = mapping
        return mapping

    def resolve_name(self, name: str) -> str:
        if not self._name_map:
            self.names()
        if name in self._name_map:
            return self._name_map[name]
        # some hosts sanitize "." to "_"; accept the canonical id if that round-trips
        dotted = name.replace("_", ".")
        if dotted in self._name_map:
            return self._name_map[dotted]
        reg = self.toolkit.engine.registry
        near = reg.suggest(name) or reg.suggest(dotted)
        raise SkeletonKeyError(
            E.UNKNOWN_TOOL, f"unknown tool {name!r} on this server",
            details={"requested": name, "suggested": near,
                     "hint": "use tools/list; names are capability-gated"},
            next_actions=[{"tool": "registry.search", "args": {"query": dotted, "limit": 5}}]
            if not near else [{"tool": near[0]["id"], "note": "close match"}],
        )

    def advertise(self) -> Any:
        cfg = self.toolkit.config
        return self.engine.advertise(token_budget=cfg.mcp.advertise_max_tools * 220,
                                    dedupe_capability=True)


def build_server(toolkit: Any, *, include_resources: bool = True, include_prompts: bool = True) -> tuple:
    """Returns (server, init_options, bridge)."""
    from mcp.server.lowlevel import Server
    from mcp.server.models import InitializationOptions
    from mcp_types import (
        CallToolRequestParams,
        CallToolResult,
        GetPromptRequestParams,
        GetPromptResult,
        ListPromptsRequest,
        ListPromptsResult,
        ListResourcesRequest,
        ListResourcesResult,
        ListToolsRequest,
        ListToolsResult,
        Prompt,
        PromptMessage,
        ReadResourceRequestParams,
        ReadResourceResult,
        Resource,
        TextContent,
        TextResourceContents,
        ToolAnnotations,
        # NB: `Role` is Literal["user", "assistant"], not an enum - `role=Role.USER`
        # raises at call time, so the literal string is used below.
    )
    from mcp_types import (
        Tool as MTool,
    )

    bridge = McpBridge(toolkit)
    cfg = toolkit.config

    @asynccontextmanager
    async def lifespan(_server: Any):
        yield {"bridge": bridge, "toolkit": toolkit}

    server: Server = Server(SERVER_TITLE, version=__version__, instructions=INSTRUCTIONS,
                            lifespan=lifespan)

    # -------------------------------------------------------------------- tools
    async def on_list_tools(_ctx: Any, _req: ListToolsRequest) -> ListToolsResult:
        snap = bridge.advertise()
        bridge._digest = snap.digest
        tools: list[MTool] = []
        for man in snap.tools:
            desc = (man.description or "").strip()
            if man.anti_patterns:
                desc += "\nAvoid: " + "; ".join(man.anti_patterns[:2])
            if man.see_also:
                desc += "\nSee also: " + ", ".join(man.see_also[:3])
            ann = ToolAnnotations(
                title=man.title,
                readOnlyHint=man.risk in ("none", "read"),
                destructiveHint=bool(man.destructive),
                idempotentHint=bool(man.idempotent),
                openWorldHint=bool(man.open_world),
            )
            tools.append(MTool(
                name=man.id, title=man.title, description=desc[:1200],
                input_schema=man.input_schema_for_host(), annotations=ann,
                output_schema=man.output_schema,
                _meta={"sk": {"id": man.id, "risk": man.risk, "capability": man.capability,
                              "group": man.group, "tags": man.tags, "provider": man.provider,
                              "reversible": man.reversible, "stateful": man.stateful,
                              "typical_latency_ms": man.typical_latency_ms,
                              "requires": [r.to_dict() for r in man.requirements]}},
            ))
        return ListToolsResult(tools=tools, meta={
            "sk.digest": snap.digest, "sk.tokens_estimate": snap.tokens,
            "sk.registered": len(bridge.engine.registry.all()),
            "sk.selected_providers": snap.selected})

    async def on_call_tool(ctx: Any, req: CallToolRequestParams) -> CallToolResult:
        args = dict(req.arguments or {})
        meta = getattr(req, "meta", None)
        extra = None
        if meta is not None:
            extra = getattr(meta, "additional_properties", None) or getattr(meta, "model_extra", None)
        if isinstance(extra, dict) and isinstance(extra.get("sk"), dict):
            sk = extra["sk"]
            if sk.get("task_id"):
                bridge.ctx.task_id = str(sk["task_id"])
        try:
            tool_id = bridge.resolve_name(req.name)
        except SkeletonKeyError as exc:
            return _error_result(exc.err.to_dict(), req.name)

        res = bridge.engine.call(tool_id, args, ctx=bridge.ctx,
                                 max_output_bytes=cfg.budget.max_output_bytes)
        payload = res.to_dict(max_bytes=cfg.budget.max_output_bytes, spill_dir=cfg.budget.spill_dir)
        text = compact_json(payload)
        structured = {k: v for k, v in payload.items() if k in ("ok", "data", "error", "hints",
                                                                "next_actions", "artifacts", "warnings",
                                                                "metrics", "context")}
        # APPROVAL_REQUIRED is returned as a coded envelope carrying the prompt
        # payload; real MCP elicitation (a protocol-level ask) is Phase 3.
        return CallToolResult(content=[TextContent(type="text", text=text)],
                              structuredContent=structured, isError=not res.ok)

    def _error_result(err: dict[str, Any], name: str) -> CallToolResult:
        payload = {"ok": False, "tool": name, "error": err}
        return CallToolResult(content=[TextContent(type="text", text=compact_json(payload))],
                              structuredContent=payload, isError=True)

    server.add_request_handler("tools/list", ListToolsRequest, on_list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, on_call_tool)

    # ------------------------------------------------------------------ prompts
    if include_prompts:
        async def on_list_prompts(_ctx: Any, _req: ListPromptsRequest) -> ListPromptsResult:
            prompts = [Prompt(name="capability_report", title="Capability report",
                              description="What this host can do and which tools are gated, as JSON.",
                              arguments=[]),
                       Prompt(name="task_bootstrap", title="Task bootstrap",
                              description="Skills matched to a task + the advertised tool set, ready to inject.",
                              arguments=[{"name": "task", "required": True,
                                           "description": "One or two sentences describing the job"}])]
            for skill in toolkit.skills.discover():
                prompts.append(Prompt(
                    name=f"skill_{skill.name.replace('-', '_')}", title=skill.name,
                    description=(skill.description or skill.when_to_use or f"skill {skill.name}")[:400],
                    arguments=[{"name": "task", "required": False,
                                "description": "Optional task text; used to pick reference files to inline"}]))
            return ListPromptsResult(prompts=prompts)

        async def on_get_prompt(_ctx: Any, req: GetPromptRequestParams) -> GetPromptResult:
            args = dict(req.arguments or {})
            if req.name == "capability_report":
                return GetPromptResult(description="host capabilities",
                                       messages=[PromptMessage(role="user", content=TextContent(
                                           type="text", text=compact_json(toolkit.describe())))])
            if req.name == "task_bootstrap":
                task = str(args.get("task", ""))
                payload = {"task": task, "skills": toolkit.skills.context_block(task),
                           "tools": [m.to_dict(include_schema=False) for m in bridge.advertise().tools],
                           "shells": {"available": toolkit.profile.available_dialects(),
                                      "preferred": toolkit.profile.preferred_dialect()}}
                return GetPromptResult(description="bootstrap context",
                                       messages=[PromptMessage(role="user", content=TextContent(
                                           type="text", text=compact_json(payload)))])
            skill_name = req.name.removeprefix("skill_").replace("_", "-")
            try:
                skill = toolkit.skills.get(skill_name)
            except SkeletonKeyError as exc:
                return GetPromptResult(description=f"unknown skill: {exc.err.message}", messages=[])
            refs: list[str] = []
            if args.get("task"):
                words = {w for w in str(args["task"]).lower().split() if len(w) > 3}
                refs = [r for r in skill.references if any(w in r.lower() for w in words)][:2]
            body = skill.render_injection(max_tokens=2000, with_references=refs)
            return GetPromptResult(description=skill.description or skill.name,
                                   messages=[PromptMessage(role="user",
                                                           content=TextContent(type="text", text=body))])

        server.add_request_handler("prompts/list", ListPromptsRequest, on_list_prompts)
        server.add_request_handler("prompts/get", GetPromptRequestParams, on_get_prompt)

    # ---------------------------------------------------------------- resources
    if include_resources:
        async def on_list_resources(_ctx: Any, _req: ListResourcesRequest) -> ListResourcesResult:
            items = [
                Resource(uri="skeletonkey://profile", name="Capability profile",
                         description="Host probes: OS, shells, binaries, filesystem traits, gates.",
                         mimeType="application/json"),
                Resource(uri="skeletonkey://tools", name="Advertised tools",
                         description="Current tool set with risk class and selected providers.",
                         mimeType="application/json"),
                Resource(uri="skeletonkey://journal", name="Change journal",
                         description="Recent journaled mutations with undo tokens.", mimeType="application/json"),
                Resource(uri="skeletonkey://ledger", name="Call ledger",
                         description="Tail of the append-only call ledger (hash-chained).",
                         mimeType="application/x-ndjson"),
            ]
            root = toolkit.config.workspace
            count = 0
            for dirpath, dirnames, filenames in os.walk(root):
                rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
                dirnames[:] = [d for d in dirnames if not d.startswith(".")
                               and not toolkit.sandbox.should_ignore(
                                   f"{rel_dir}/{d}" if rel_dir != "." else d)]
                if rel_dir.count("/") >= 2:
                    dirnames[:] = []
                for fn in filenames[:150]:
                    rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                    if toolkit.sandbox.should_ignore(rel):
                        continue
                    items.append(Resource(uri=f"skeletonkey://file/{rel}", name=rel,
                                          mimeType="text/plain", description="workspace file"))
                    count += 1
                    if count > 300:
                        return ListResourcesResult(resources=items)
            return ListResourcesResult(resources=items)

        async def on_read_resource(_ctx: Any, req: ReadResourceRequestParams) -> ReadResourceResult:
            uri = str(req.uri)
            if uri == "skeletonkey://profile":
                text = compact_json(toolkit.profile.to_dict(include_receipts=False))
            elif uri == "skeletonkey://tools":
                text = compact_json([m.to_dict(include_schema=False) for m in bridge.advertise().tools])
            elif uri == "skeletonkey://journal":
                text = compact_json(toolkit.journal.list(limit=100))
            elif uri == "skeletonkey://ledger":
                text = _tail_file(toolkit.ledger.path, 64_000) if toolkit.ledger else ""
            elif uri.startswith("skeletonkey://file/"):
                rel = uri.removeprefix("skeletonkey://file/")
                try:
                    text = toolkit.fs.read(rel, limit_lines=2000).content
                except SkeletonKeyError as exc:
                    from mcp_types import McpError

                    raise McpError(f"cannot read resource: {exc.err.code}: {exc.err.message}") from None
            else:
                from mcp_types import McpError

                raise McpError(f"unknown resource {uri}")
            return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="text/plain",
                                                                     text=text[:400_000])])

        server.add_request_handler("resources/list", ListResourcesRequest, on_list_resources)
        server.add_request_handler("resources/read", ReadResourceRequestParams, on_read_resource)

    # Advertise tools.listChanged: the tool set moves when the profile is re-probed
    # (a laptop plugs in, pwsh gets installed), and `notify_tools_changed` below sends
    # it. Claiming `false` would make spec-following hosts drop that notification.
    try:
        from mcp.server.lowlevel.server import NotificationOptions as _NotifOptions

        notif_options = _NotifOptions(tools_changed=True)
    except Exception:  # pragma: no cover - older/absent SDK shape
        notif_options = None

    init_options = InitializationOptions(
        server_name=SERVER_TITLE, server_version=__version__,
        capabilities=server.get_capabilities(
            notification_options=notif_options,
            experimental_capabilities={"skeletonkey": {"version": __version__, "dynamic_tools": True,
                                                        "profile_resource": "skeletonkey://profile"}}))
    return server, init_options, bridge


def _tail_file(path: str, nbytes: int) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


async def notify_tools_changed(bridge: McpBridge, session: Any) -> bool:
    """Send tools/list_changed if the advertisement actually moved."""
    snap = bridge.advertise()
    if snap.digest == bridge._digest or session is None:
        return False
    bridge._digest = snap.digest
    try:
        await session.send_tool_list_changed()
        return True
    except Exception:
        return False


def make_approval_callback(*, auto: bool = False, echo: bool = True) -> Any:
    """Default approver for stdio hosts with no UI: deny-with-reason, never guess.

    `auto=True` (SKELETONKEY_AUTO_APPROVE=1) is the explicit autopilot dial.
    """
    def _approve(req: ApprovalRequired) -> bool:
        if auto:
            return True
        if echo:
            print(f"[skeletonkey] approval required: {req.tool} ({req.risk}) - {req.reason}",
                  flush=True)
        return False
    return _approve
