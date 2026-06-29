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

import json
import logging
import os
import secrets
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from starlette.responses import Response
from zta.agent_graph import build_zta_graph
from zta.audit import Audit
from zta.errors import RbacError
from zta.rbac import KNOWN_PAGES, Permissions, Role
from zta.runtime import session
from zta.users import UserStore
from zta.webauth import sign_session, verify_session

load_dotenv()

_log = logging.getLogger(__name__)

SESSION_COOKIE = "zta_session"


class AppConfig(BaseModel):
    agent_id: str = "analyst-bot"
    policy_path: Path = Field(default_factory=lambda: Path("./policy.yaml"))
    audit_path: Path = Field(default_factory=lambda: Path("./audit.jsonl"))
    key_dir: Path = Field(default_factory=lambda: Path("./.zta/keys"))
    roles_path: Path = Field(default_factory=lambda: Path("./roles.yaml"))
    users_db_path: Path = Field(
        default_factory=lambda: Path(os.environ.get("ZTA_DB_PATH", "./data.db"))
    )
    secret_key: str = Field(default_factory=lambda: os.environ.get("ZTA_SECRET_KEY", ""))


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]


class ChatResponse(BaseModel):
    reply: str
    trace: list[dict[str, object]]


@tool("echo")
def _echo(message: str) -> str:
    """Echo the user's message back."""
    return f"echo: {message}"


def _make_db_query(allowed_tables: set[str] | None, role: str) -> BaseTool:
    """Build a db_query tool scoped to `allowed_tables` (None = all tables).

    When scoped, a SQLite authorizer denies reads of any table outside the set
    at the engine level; the denial is surfaced as an RbacError so the runtime
    records a clean authorization deny.
    """

    @tool("db_query")
    def db_query(sql: str) -> list[dict[str, object]]:
        """Execute a read-only SQL query against the Chinook media-store database.

        Only SELECT and WITH statements are allowed.
        Schema (SQLite, Chinook v1.4.5):
          Artist(ArtistId, Name)
          Album(AlbumId, Title, ArtistId -> Artist)
          Track(TrackId, Name, AlbumId -> Album, MediaTypeId -> MediaType,
                GenreId -> Genre, Composer, Milliseconds, Bytes, UnitPrice)
          Genre(GenreId, Name); MediaType(MediaTypeId, Name)
          Playlist(PlaylistId, Name); PlaylistTrack(PlaylistId -> Playlist, TrackId -> Track)
          Customer(CustomerId, FirstName, LastName, Email, Country, SupportRepId -> Employee)
          Employee(EmployeeId, FirstName, LastName, Title, ReportsTo -> Employee)
          Invoice(InvoiceId, CustomerId -> Customer, InvoiceDate, BillingCountry, Total)
          InvoiceLine(InvoiceLineId, InvoiceId -> Invoice, TrackId -> Track, UnitPrice, Quantity)
        """
        db_path = Path(os.environ.get("ZTA_DB_PATH", "./data.db"))
        conn = sqlite3.connect(str(db_path))
        denied: list[str] = []

        def authorizer(
            action: int, arg1: str | None, _arg2: str | None, _db: str | None, _trig: str | None
        ) -> int:
            if (
                allowed_tables is not None
                and action == sqlite3.SQLITE_READ
                and arg1 is not None
                and arg1 not in allowed_tables
            ):
                denied.append(arg1)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        try:
            conn.row_factory = sqlite3.Row
            if allowed_tables is not None:
                conn.set_authorizer(authorizer)
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.DatabaseError as exc:
            if denied:
                raise RbacError(
                    f"rbac: role {role!r} not permitted to read table {denied[0]!r}"
                ) from exc
            raise
        finally:
            conn.close()

    return db_query


@tool("db_write")
def _db_write(sql: str) -> str:  # pragma: no cover -- denied by policy
    """Write to the database. Disabled for analyst-bot in the MVP."""
    return "db_write is not permitted in this demo"


def _tools_for(permissions: Permissions, role: str) -> list[BaseTool]:
    """Per-request tool list with db_query scoped to the role's readable tables."""
    return [_make_db_query(permissions.tables_allowed(role), role), _db_write, _echo]


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


def _sse_event(event_type: str, data: dict[str, object]) -> str:
    """Format a single Server-Sent Event payload."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _current_user(request: Request, cfg: AppConfig) -> dict[str, str] | None:
    """Return the signed-in user payload from the session cookie, or None."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return verify_session(token, cfg.secret_key)


def _allowed_pages(perms: Permissions, role: str) -> list[str]:
    return [p for p in sorted(KNOWN_PAGES) if perms.page_allowed(role, p)]


async def _stream_chat(
    messages: list[dict[str, object]],
    cfg: AppConfig,
    *,
    user: str,
    role: str,
    permissions: Permissions,
) -> AsyncIterator[str]:
    """Stream LangGraph events as SSE lines (token, trace, error, end)."""
    try:
        model = _get_chat_model()
    except HTTPException as exc:
        yield _sse_event("error", {"message": exc.detail})
        return

    with session(
        agent=cfg.agent_id,
        policy=cfg.policy_path,
        audit=cfg.audit_path,
        key_dir=cfg.key_dir,
        user=user,
        role=role,
        permissions=permissions,
    ) as agent:
        tools = _tools_for(permissions, role)
        for t in tools:
            agent.registry.register(t)
        graph = build_zta_graph(model, agent, tools)
        initial_state = {"messages": [_dict_to_message(m) for m in messages]}
        try:
            async for event in graph.astream_events(initial_state, version="v2"):
                event_type = event.get("event")
                if event_type == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    content = getattr(chunk, "content", None)
                    if content:
                        yield _sse_event("token", {"content": str(content)})
                elif event_type == "on_custom_event" and event.get("name") == "zta_trace":
                    yield _sse_event("trace", event.get("data", {}))
            yield _sse_event("end", {"done": True})
        except Exception as exc:  # pragma: no cover - defensive
            _log.exception("streaming chat failed")
            yield _sse_event("error", {"message": str(exc)})


async def _run_chat(
    messages: list[dict[str, object]],
    cfg: AppConfig,
    *,
    user: str,
    role: str,
    permissions: Permissions,
) -> tuple[list[dict[str, object]], str, list[dict[str, object]]]:
    """Run the LangGraph agent and return (final_messages, reply, trace_dicts)."""
    model = _get_chat_model()
    with session(
        agent=cfg.agent_id,
        policy=cfg.policy_path,
        audit=cfg.audit_path,
        key_dir=cfg.key_dir,
        user=user,
        role=role,
        permissions=permissions,
    ) as agent:
        tools = _tools_for(permissions, role)
        for t in tools:
            agent.registry.register(t)
        graph = build_zta_graph(model, agent, tools)
        initial_state = {"messages": [_dict_to_message(m) for m in messages]}
        final_state = await graph.ainvoke(initial_state)
        final_messages = final_state.get("messages", [])
        reply = final_messages[-1].content if final_messages else ""
        messages_out = [_message_to_dict(m) for m in final_messages]
        trace = [t.__dict__ for t in agent.trace]
        return messages_out, str(reply), trace


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or AppConfig()
    if not cfg.secret_key:
        cfg = cfg.model_copy(update={"secret_key": secrets.token_hex(32)})
        _log.warning("ZTA_SECRET_KEY not set; using an ephemeral key (sessions reset on restart)")
    perms = Permissions.load(cfg.roles_path)
    users = UserStore(cfg.users_db_path)

    app = FastAPI(title="ZTA Control Plane", version="0.1.0")
    app.state.config = cfg
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")

    def base_ctx(user: dict[str, str]) -> dict[str, object]:
        return {"current_user": user, "allowed_pages": _allowed_pages(perms, user["role"])}

    # ---------- auth ----------

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> Response:
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login_submit(request: Request) -> Response:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        user = users.get_user(username)
        if user is None or not users.verify_password(username, password):
            return templates.TemplateResponse(
                request, "login.html", {"error": "Invalid username or password"}, status_code=401
            )
        token = sign_session({"username": user.username, "role": user.role}, cfg.secret_key)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
        return resp

    @app.get("/logout")
    async def logout() -> Response:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # ---------- chat ----------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request, "chat.html", {"messages": [], "trace": [], **base_ctx(user)}
        )

    @app.post("/chat")
    async def chat(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            raise HTTPException(status_code=401, detail="login required")
        data = await request.json()
        req_messages = data.get("messages", [])
        if not req_messages:
            raise HTTPException(status_code=400, detail="messages must be non-empty")
        messages_in: list[dict[str, object]] = [
            {"role": m["role"], "content": m["content"]} for m in req_messages
        ]
        messages_out, reply, trace = await _run_chat(
            messages_in, cfg, user=user["username"], role=user["role"], permissions=perms
        )
        return JSONResponse({"reply": reply, "trace": trace, "messages": messages_out})

    @app.post("/chat/stream")
    async def chat_stream(request: Request) -> Response:
        """Stream the chat response as Server-Sent Events."""
        user = _current_user(request, cfg)
        if user is None:
            raise HTTPException(status_code=401, detail="login required")
        data = await request.json()
        req_messages = data.get("messages", [])
        if not req_messages:
            raise HTTPException(status_code=400, detail="messages must be non-empty")
        messages_in: list[dict[str, object]] = [
            {"role": m["role"], "content": m["content"]} for m in req_messages
        ]
        return StreamingResponse(
            _stream_chat(
                messages_in, cfg, user=user["username"], role=user["role"], permissions=perms
            ),
            media_type="text/event-stream",
        )

    # ---------- audit ----------

    @app.get("/api/audit")
    async def api_audit(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            raise HTTPException(status_code=401, detail="login required")
        if not perms.page_allowed(user["role"], "audit"):
            raise HTTPException(status_code=403, detail="forbidden")
        audit = Audit(cfg.audit_path)
        return JSONResponse(
            {
                "events": [e.model_dump(mode="json") for e in audit.read_all()],
                "chain_valid": audit.verify_chain(),
            }
        )

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_view(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "audit"):
            return HTMLResponse("403 Forbidden", status_code=403)
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
                **base_ctx(user),
            },
        )

    # ---------- policy ----------

    @app.get("/policy", response_class=HTMLResponse)
    async def policy_view(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "policy"):
            return HTMLResponse("403 Forbidden", status_code=403)
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
                **base_ctx(user),
            },
        )

    # ---------- admin: users & roles ----------

    @app.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "users"):
            return HTMLResponse("403 Forbidden", status_code=403)
        return templates.TemplateResponse(
            request,
            "users.html",
            {
                "users": users.list_users(),
                "roles": [r.value for r in Role],
                **base_ctx(user),
            },
        )

    @app.post("/users")
    async def users_create(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "users"):
            return HTMLResponse("403 Forbidden", status_code=403)
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        role = str(form.get("role", ""))
        valid = bool(username) and bool(password) and role in {r.value for r in Role}
        if valid and users.get_user(username) is None:
            users.create_user(username, password, role)
        return RedirectResponse("/users", status_code=303)

    @app.post("/users/delete")
    async def users_delete(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "users"):
            return HTMLResponse("403 Forbidden", status_code=403)
        form = await request.form()
        target = str(form.get("username", ""))
        if target and target != user["username"]:  # cannot delete yourself
            users.delete_user(target)
        return RedirectResponse("/users", status_code=303)

    @app.get("/roles", response_class=HTMLResponse)
    async def roles_page(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "roles"):
            return HTMLResponse("403 Forbidden", status_code=403)
        return templates.TemplateResponse(
            request, "roles.html", {"matrix": perms.as_table(), **base_ctx(user)}
        )

    return app


app = create_app()
