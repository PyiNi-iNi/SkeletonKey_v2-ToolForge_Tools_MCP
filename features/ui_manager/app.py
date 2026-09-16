"""UI layer: Gradio app factory (optional dep) + headless console UI.

``build_app`` imports Gradio lazily — without it the platform still runs
(headless UI + HTTP API). The Gradio app wires: streaming chat, bake-off
tab (side-by-side with live timing), state save/resume, and an auth gate
when an auth provider is configured.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from features.common.errors import BackendUnavailableError
from features.common.openai_compat import ChatMessage
from features.inference_engine import InferenceEngine
from features.inference_engine.backends.base import GenerationParams

from .bakeoff import BakeOff
from .state import StateStore, new_conversation  # noqa: F401  (new_conversation exported via __init__)

__all__ = ["build_app", "HeadlessUI"]


def build_app(engine: InferenceEngine | None = None,
              auth: Any | None = None, state: StateStore | None = None,
              models: Sequence[str] | None = None):
    """Return a Gradio Blocks app, or raise if Gradio is not installed."""
    try:
        import gradio as gr  # type: ignore
    except ImportError as exc:
        raise BackendUnavailableError(
            "gradio is not installed",
            hint='pip install "gradio>=4.20"',
        ) from exc

    engine = engine or InferenceEngine()
    state = state or StateStore(".skeletonkey/ui-state")
    models = list(models) if models else engine.model_ids()

    async def gr_chat(message: str, history: list, model: str):
        out: list[str] = []
        async for tok in engine.astream(
            model, [ChatMessage(role="user", content=message)], GenerationParams()
        ):
            out.append(tok)
            yield (message, "".join(out))

    async def gr_bakeoff(prompt: str) -> str:
        entries = await BakeOff(engine, models).run(prompt)
        return BakeOff.render(entries)

    with gr.Blocks(title="SkeletonKey UI") as app:
        if auth is not None:
            with gr.Tab("Login"):
                username = gr.Textbox(label="Username")
                password = gr.Textbox(label="Password", type="password")
                login_btn = gr.Button("Login")
                session = gr.Textbox(label="Session token", visible=False)
                login_msg = gr.Markdown()

                def do_login(u: str, p: str, tok: str = ""):
                    try:
                        new_tok = auth.login(u, p)
                        return new_tok, "Logged in.", new_tok
                    except Exception as exc:  # noqa: BLE001
                        return tok, f"Login failed: {exc}", tok

                login_btn.click(do_login, [username, password, session],
                                [session, login_msg, session])
        with gr.Tab("Chat"):
            gr.Markdown("Streaming chat (80 tok/s target)")
            model_dd = gr.Dropdown(models, value=models[0] if models else "mock-1b",
                                   label="Model", interactive=True)
            chatbox = gr.Chatbot(label="Conversation")
            user_in = gr.Textbox(label="Message")
            send = gr.Button("Send")
            send.click(gr_chat, [user_in, chatbox, model_dd], [chatbox])
            user_in.submit(gr_chat, [user_in, chatbox, model_dd], [chatbox])
        with gr.Tab("Bake-off"):
            gr.Markdown("Run one prompt across all models, side by side")
            bo_in = gr.Textbox(label="Prompt")
            bo_out = gr.Markdown()
            bo_btn = gr.Button("Run bake-off")
            bo_btn.click(gr_bakeoff, [bo_in], [bo_out])
        with gr.Tab("State"):
            gr.Markdown("Preserve and resume session state")
            save_in = gr.Textbox(label="User to save as")
            load_in = gr.Textbox(label="User to load")
            state_out = gr.Textbox(label="Result")
            save_btn = gr.Button("Save state")
            load_btn = gr.Button("Load state")

            def do_save(user: str) -> str:
                state.save(user, {"model": model_dd.value})
                return f"state saved for {user}"

            def do_load(user: str) -> str:
                loaded = state.load(user)
                return f"state loaded: {loaded}" if loaded else f"no state for {user}"

            save_btn.click(do_save, [save_in], [state_out])
            load_btn.click(do_load, [load_in], [state_out])

    return app


class HeadlessUI:
    """Terminal UI over the same cores: chat, bake-off, state, models.

    Driven by ``repl(lines)`` so it is fully testable without a tty.
    """

    BANNER = (
        "SkeletonKey headless UI. Commands: /models, /model <name>, "
        "/bakeoff <prompt>, /state save <user>, /state load <user>, /quit"
    )

    def __init__(self, engine: InferenceEngine | None = None,
                 models: Sequence[str] | None = None, state: StateStore | None = None):
        self.engine = engine or InferenceEngine()
        self.models = list(models) if models else self.engine.model_ids()
        self.state = state or StateStore(".skeletonkey/ui-state")
        self.active_model: str = self.models[0] if self.models else "mock-1b"

    async def handle(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        if line == "/quit":
            return "(bye)"
        if line == "/models":
            return "\n".join(self.engine.model_ids())
        if line.startswith("/model "):
            name = line.split(None, 1)[1].strip()
            self.active_model = name
            return f"model -> {name}"
        if line.startswith("/bakeoff"):
            prompt = line.split(None, 1)[1].strip() if len(line) > 8 else ""
            if not prompt:
                return "usage: /bakeoff <prompt>"
            entries = await BakeOff(self.engine, self.models).run(prompt)
            return BakeOff.render(entries)
        if line.startswith("/state save "):
            user = line.split(None, 2)[2].strip()
            self.state.save(user, {"model": self.active_model, "saved": True})
            return f"state saved for {user}"
        if line.startswith("/state load "):
            user = line.split(None, 2)[2].strip()
            loaded = self.state.load(user)
            if loaded is None:
                return f"no saved state for {user}"
            if loaded.get("model"):
                self.active_model = loaded["model"]
            return f"state loaded: {loaded}"
        if line.startswith("/"):
            return f"unknown command: {line.split()[0]}"
        # plain chat
        return await self.engine.generate(
            self.active_model, [ChatMessage(role="user", content=line)],
            GenerationParams(),
        )

    async def repl(self, lines: Iterable[str]) -> list[str]:
        out = [self.BANNER]
        for line in lines:
            reply = await self.handle(line)
            if reply:
                out.append(reply)
            if line.strip() == "/quit":
                break
        return out
