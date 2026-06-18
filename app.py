"""FastAPI app for the ZTA demo runtime.

This file lives at the repo root (not under `zta/`) because it is the
demo service that USES the ZTA library. F7 shipped the JSON skeleton;
F8 wired OpenAI function calling; F9 added the Jinja2 chat UI; F10
and F11 wrapped the audit and policy endpoints in templates; F12
adds db_query/db_write tools, the seed script, and e2e smoke.

`load_dotenv()` is called at import time so `uvicorn app:app` picks up
`ZTA_OPENAI_API_KEY` (and friends) from a local `.env` without
requiring `export`. The `.env` file is git-ignored; the committed
template is `.env.example`.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from pydantic import BaseModel, Field
from starlette.responses import Response
from zta.audit import Audit
from zta.runtime import session

load_dotenv()

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


def _db_query(sql: str) -> list[dict[str, object]]:
    """Execute a SELECT (or WITH) and return rows as list of dicts."""
    db_path = Path(os.environ.get("ZTA_DB_PATH", "./data.db"))
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _db_write(sql: str) -> str:  # pragma: no cover -- denied by policy
    """Stub. Never called because policy denies db_write in MVP."""
    return "db_write is not permitted in this demo"


_TOOL_SCHEMAS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "db_query",
            "description": (
                "Execute a read-only SQL query against the analyst database. "
                "Only SELECT and WITH statements are allowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_write",
            "description": "Write to the database. Disabled for analyst-bot.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
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
        agent.registry.register(_db_query, name="db_query")
        agent.registry.register(_db_write, name="db_write")
        for _ in range(MAX_TOOL_ITERATIONS):
            completion = client.chat.completions.create(
                model=model, messages=messages, tools=_TOOL_SCHEMAS
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
