"""FastAPI app skeleton for the ZTA demo runtime.

This file lives at the repo root (not under `zta/`) because it is the
demo service that USES the ZTA library. F7 ships JSON responses on
all 5 routes; F9-F11 wrap them in Jinja templates; F8 replaces the
placeholder `echo` tool with real OpenAI function calling.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from zta.audit import Audit
from zta.runtime import session

_log = logging.getLogger(__name__)


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
    """Placeholder tool used by /chat in F7. F8 will replace with OpenAI."""
    return f"echo: {message}"


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
        last_user = next(
            (m["content"] for m in reversed(req.messages) if m.get("role") == "user"),
            None,
        )
        if last_user is None:
            raise HTTPException(status_code=400, detail="no user message found")
        with session(
            agent=cfg.agent_id,
            policy=cfg.policy_path,
            audit=cfg.audit_path,
            key_dir=cfg.key_dir,
        ) as agent:
            agent.registry.register(_echo, name="echo")
            result = agent.tool("echo", message=last_user)
        return ChatResponse(
            reply=result.value if result.ok else (result.error or ""),
            trace=[t.__dict__ for t in agent.trace],
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
