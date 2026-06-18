"""FastAPI app for the ZTA demo runtime.

This file lives at the repo root (not under `zta/`) because it is the
demo service that USES the ZTA library. F7 shipped the JSON skeleton;
F8 wired OpenAI function calling; F9 adds the Jinja2 chat UI; F10
and F11 wrap the audit and policy endpoints in templates; F12 adds
the demo seed and end-to-end smoke.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from pydantic import BaseModel, Field
from starlette.responses import Response
from zta.audit import Audit
from zta.runtime import session

_log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10


class AppConfig(BaseModel):
    agent_id: str = "analyst-bot"
    policy_path: Path = Field(default_factory=lambda: Path("./policy.yaml"))
    audit_path: Path = Field(default_factory=lambda: Path("./audit.jsonl"))
    key_dir: Path = Field(default_factory=lambda: Path("./.zta/keys"))


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]


class ChatResponse(BaseModel):
    reply: str
    trace: list[dict[str, object]]


def _echo(message: str) -> str:
    return f"echo: {message}"


_ECHO_TOOL_SCHEMA: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo the message back to the user.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    }
]


def _get_openai_client() -> OpenAI:
    api_key = os.environ.get("ZTA_OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500, detail="ZTA_OPENAI_API_KEY environment variable is not set"
        )
    return OpenAI(api_key=api_key)


def _run_chat_loop(
    messages: list[dict[str, object]],
    cfg: AppConfig,
) -> tuple[list[dict[str, object]], str, list[dict[str, object]]]:
    """Run the OpenAI tool-calling loop. Returns (final_messages, reply, trace_dicts)."""
    client = _get_openai_client()
    model = os.environ.get("ZTA_OPENAI_MODEL", "gpt-4o-mini")
    with session(
        agent=cfg.agent_id,
        policy=cfg.policy_path,
        audit=cfg.audit_path,
        key_dir=cfg.key_dir,
    ) as agent:
        agent.registry.register(_echo, name="echo")
        for _ in range(MAX_TOOL_ITERATIONS):
            completion = client.chat.completions.create(
                model=model, messages=messages, tools=_ECHO_TOOL_SCHEMA
            )
            msg = completion.choices[0].message
            if not msg.tool_calls:
                messages.append({"role": "assistant", "content": msg.content or ""})
                return messages, msg.content or "", [t.__dict__ for t in agent.trace]
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                fn_args = json.loads(tc.function.arguments)
                result = agent.tool(tc.function.name, **fn_args)
                tool_content = str(result.value) if result.ok else (result.error or "")
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_content})
    raise HTTPException(
        status_code=500, detail=f"too many tool iterations (>{MAX_TOOL_ITERATIONS})"
    )


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or AppConfig()
    app = FastAPI(title="ZTA Control Plane", version="0.1.0")
    app.state.config = cfg
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        return templates.TemplateResponse(request, "chat.html", {"messages": [], "trace": []})

    @app.post("/chat")
    async def chat(request: Request) -> Response:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            data = await request.json()
            req_messages = data.get("messages", [])
            if not req_messages:
                raise HTTPException(status_code=400, detail="messages must be non-empty")
            messages_in: list[dict[str, object]] = [
                {"role": m["role"], "content": m["content"]} for m in req_messages
            ]
            messages_out, reply, trace = _run_chat_loop(messages_in, cfg)
            return JSONResponse({"reply": reply, "trace": trace, "messages": messages_out})
        form = await request.form()
        message = form.get("message", "").strip() if form.get("message") else ""
        if not message:
            raise HTTPException(status_code=400, detail="message must be non-empty")
        display_messages: list[dict[str, str]] = [{"role": "user", "content": message}]
        messages_in = [{"role": "user", "content": message}]
        messages_out, reply, trace = _run_chat_loop(messages_in, cfg)
        display_messages.append({"role": "assistant", "content": str(reply)})
        return templates.TemplateResponse(
            request,
            "chat.html",
            {"messages": display_messages, "trace": trace},
        )

    @app.get("/api/audit")
    async def api_audit() -> dict[str, object]:
        audit = Audit(cfg.audit_path)
        return {
            "events": [e.model_dump(mode="json") for e in audit.read_all()],
            "chain_valid": audit.verify_chain(),
        }

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_view(request: Request) -> Response:
        audit = Audit(cfg.audit_path)
        events = audit.read_all()
        events_sorted = list(reversed(events))
        return templates.TemplateResponse(
            request,
            "audit.html",
            {
                "chain_valid": audit.verify_chain(),
                "chain_status": "valid" if audit.verify_chain() else "tampered",
                "events": [e.model_dump(mode="json") for e in events_sorted],
                "event_count": len(events),
            },
        )

    @app.get("/policy")
    async def policy_view() -> dict[str, str]:
        if not cfg.policy_path.exists():
            raise HTTPException(status_code=500, detail=f"policy file not found: {cfg.policy_path}")
        return {"content": cfg.policy_path.read_text()}

    return app


app = create_app()
