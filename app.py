"""FastAPI app skeleton for the ZTA demo runtime.

This file lives at the repo root (not under `zta/`) because it is the
demo service that USES the ZTA library. F7 ships JSON responses on
all 5 routes; F9-F11 wrap them in Jinja templates; F8 replaces the
placeholder `echo` tool with real OpenAI function calling.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field
from zta.audit import Audit
from zta.runtime import session

_log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10


class AppConfig(BaseModel):
    """Configuration for the FastAPI app. Override per-test via create_app()."""

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
    """Placeholder tool used by /chat. F12 will add db_query and db_write."""
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


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build the FastAPI app with the 5 demo routes."""
    cfg = config or AppConfig()
    app = FastAPI(title="ZTA Control Plane", version="0.1.0")
    app.state.config = cfg

    @app.get("/")
    async def index() -> dict[str, str]:
        return {"service": "zta-controlplane", "version": "0.1.0"}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        if not req.messages:
            raise HTTPException(status_code=400, detail="messages must be non-empty")
        client = _get_openai_client()
        model = os.environ.get("ZTA_OPENAI_MODEL", "gpt-4o-mini")
        messages: list[dict[str, object]] = [
            {"role": m["role"], "content": m["content"]} for m in req.messages
        ]
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
                    return ChatResponse(
                        reply=msg.content or "",
                        trace=[t.__dict__ for t in agent.trace],
                    )
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
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": tool_content}
                    )
        raise HTTPException(
            status_code=500, detail=f"too many tool iterations (>{MAX_TOOL_ITERATIONS})"
        )

    @app.get("/api/audit")
    async def api_audit() -> dict[str, object]:
        audit = Audit(cfg.audit_path)
        return {
            "events": [e.model_dump(mode="json") for e in audit.read_all()],
            "chain_valid": audit.verify_chain(),
        }

    @app.get("/audit")
    async def audit_view() -> dict[str, object]:
        return await api_audit()

    @app.get("/policy")
    async def policy_view() -> dict[str, str]:
        if not cfg.policy_path.exists():
            raise HTTPException(status_code=500, detail=f"policy file not found: {cfg.policy_path}")
        return {"content": cfg.policy_path.read_text()}

    return app


app = create_app()
