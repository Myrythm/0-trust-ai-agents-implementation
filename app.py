"""FastAPI app for the ZTA demo runtime.

This file lives at the repo root (not under `zta/`) because it is the
demo service that USES the ZTA library. F7 shipped the JSON skeleton;
F8 wired OpenAI function calling (since swapped to LangChain ChatOpenAI); F9 added the Jinja2 chat UI; F10
and F11 wrapped the audit and policy endpoints in templates; F12
adds db_query/db_write tools, the seed script, and e2e smoke.

`load_dotenv()` is called at import time so `uvicorn app:app` picks up
`ZTA_OPENAI_API_KEY` (and friends) from a local `.env` without
requiring `export`. The `.env` file is git-ignored; the committed
template is `.env.example`.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from starlette.responses import Response
from zta.agent_graph import build_zta_graph
from zta.audit import Audit
from zta.runtime import session

load_dotenv()

_log = logging.getLogger(__name__)


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


@tool("echo")
def _echo(message: str) -> str:
    """Echo the user's message back."""
    return f"echo: {message}"


@tool("db_query")
def _db_query(sql: str) -> list[dict[str, object]]:
    """Execute a read-only SQL query against the analyst database.

    Only SELECT and WITH statements are allowed.
    Schema: customers(id, name, email); orders(id, customer_id, amount, placed_on)
    with orders.customer_id -> customers.id.
    """
    db_path = Path(os.environ.get("ZTA_DB_PATH", "./data.db"))
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@tool("db_write")
def _db_write(sql: str) -> str:  # pragma: no cover -- denied by policy
    """Write to the database. Disabled for analyst-bot in the MVP."""
    return "db_write is not permitted in this demo"


_TOOLS: list[BaseTool] = [_db_query, _db_write, _echo]


def _get_chat_model() -> ChatOpenAI:
    api_key = os.environ.get("ZTA_OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500, detail="ZTA_OPENAI_API_KEY environment variable is not set"
        )
    model = os.environ.get("ZTA_OPENAI_MODEL", "gpt-4o-mini")
    return ChatOpenAI(model=model, api_key=api_key)


def _dict_to_message(message: dict[str, object]) -> BaseMessage:
    """Convert an OpenAI-style message dict to a LangChain BaseMessage."""
    role = message.get("role")
    content = str(message.get("content", ""))
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    if role == "tool":
        return ToolMessage(content=content, tool_call_id=str(message.get("tool_call_id", "")))
    return HumanMessage(content=content)


def _message_to_dict(message: BaseMessage) -> dict[str, object]:
    """Convert a LangChain BaseMessage back to an OpenAI-style dict."""
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AIMessage):
        return {"role": "assistant", "content": message.content}
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
    return {"role": "assistant", "content": str(message.content)}


async def _run_chat(
    messages: list[dict[str, object]],
    cfg: AppConfig,
) -> tuple[list[dict[str, object]], str, list[dict[str, object]]]:
    """Run the LangGraph agent and return (final_messages, reply, trace_dicts)."""
    model = _get_chat_model()
    with session(
        agent=cfg.agent_id,
        policy=cfg.policy_path,
        audit=cfg.audit_path,
        key_dir=cfg.key_dir,
    ) as agent:
        for t in _TOOLS:
            agent.registry.register(t)
        graph = build_zta_graph(model, agent, _TOOLS)
        initial_state = {"messages": [_dict_to_message(m) for m in messages]}
        final_state = await graph.ainvoke(initial_state)
        final_messages = final_state.get("messages", [])
        reply = final_messages[-1].content if final_messages else ""
        messages_out = [_message_to_dict(m) for m in final_messages]
        trace = [t.__dict__ for t in agent.trace]
        return messages_out, str(reply), trace


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
            messages_out, reply, trace = await _run_chat(messages_in, cfg)
            return JSONResponse({"reply": reply, "trace": trace, "messages": messages_out})
        form = await request.form()
        message = form.get("message", "").strip() if form.get("message") else ""
        if not message:
            raise HTTPException(status_code=400, detail="message must be non-empty")
        display_messages: list[dict[str, str]] = [{"role": "user", "content": message}]
        messages_in = [{"role": "user", "content": message}]
        messages_out, reply, trace = await _run_chat(messages_in, cfg)
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

    @app.get("/policy", response_class=HTMLResponse)
    async def policy_view(request: Request) -> Response:
        if not cfg.policy_path.exists():
            return HTMLResponse(
                f"<html><body><h1>500</h1><p>policy file not found: {cfg.policy_path}</p></body></html>",
                status_code=500,
            )
        import yaml as _yaml

        raw = cfg.policy_path.read_text()
        try:
            parsed = _yaml.safe_load(raw) or {}
        except _yaml.YAMLError as exc:
            return HTMLResponse(
                f"<html><body><h1>500</h1><p>invalid YAML: {exc}</p></body></html>",
                status_code=500,
            )
        return templates.TemplateResponse(
            request,
            "policy.html",
            {
                "agent": parsed.get("agent"),
                "content": raw,
                "rules": parsed.get("rules") or [],
                "default": parsed.get("default", "deny"),
            },
        )

    return app


app = create_app()
