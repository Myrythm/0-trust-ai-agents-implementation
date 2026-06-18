# Feature 9: Jinja2 Chat UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON `GET /` with a Jinja2 chat page. `POST /chat` becomes a form endpoint that re-renders `chat.html` with the new user message, the LLM reply, and the inline tool-call trace. JSON API mode is preserved for programmatic clients.

**Architecture:** Add `templates/base.html` + `templates/chat.html` + `static/style.css`. The FastAPI app uses `Jinja2Templates` and `StaticFiles`. The `/chat` endpoint inspects the request `Content-Type`:
- `application/x-www-form-urlencoded` → HTMLResponse with rendered chat.html
- `application/json` → JSONResponse (existing F8 behavior)

F12 will add `static/app.js` for polling `/api/audit`. F10 wraps `/audit` in a template. F11 wraps `/policy` in a template.

**Tech Stack:** `jinja2` (already pinned), `fastapi.staticfiles` (part of FastAPI). No new deps.

---

## File Structure

```
templates/
├── base.html
└── chat.html
static/
└── style.css
app.py                       # modify: add Jinja setup, change GET /, update /chat
tests/test_app.py            # add 5 new tests for the HTML flow
```

---

## Contract

**`GET /`** → `chat.html` rendered with `messages=[]`, `trace=[]` (empty chat on first load).

**`POST /chat` with `Content-Type: application/x-www-form-urlencoded`:**
- `message=<text>` field
- If empty/missing → 400 with HTML error
- Else: run OpenAI loop, render `chat.html` with `messages=[user_msg, assistant_msg]`, `trace=[...]`

**`POST /chat` with `Content-Type: application/json`:** unchanged (F8 behavior).

**Template variables for `chat.html`:**
- `messages`: list of `{"role": "user"|"assistant", "content": "..."}` for display
- `trace`: list of `{"tool": str, "decision": str, "reason": str, "ok": bool, ...}`

**`base.html` blocks:** `title`, `content` (for chat.html to override).

**`style.css`:** minimal — no framework. Class names: `.messages`, `.message`, `.message-user`, `.message-assistant`, `.trace`, `.trace-entry`, `.trace-allow`, `.trace-deny`, `.trace-error`, `.badge`.

---

## Tasks

### Task 1: Create `templates/base.html` and `templates/chat.html`

**Files:**
- Create: `templates/base.html`
- Create: `templates/chat.html`

- [ ] **Step 1: Create `templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{% block title %}ZTA Control Plane{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header>
    <nav>
      <a href="/">Chat</a>
      <a href="/audit">Audit</a>
      <a href="/policy">Policy</a>
    </nav>
  </header>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 2: Create `templates/chat.html`**

```html
{% extends "base.html" %}
{% block title %}ZTA Chat{% endblock %}
{% block content %}
<h1>ZTA Chat</h1>
<form method="post" action="/chat" class="chat-form">
  <textarea name="message" rows="3" required placeholder="Ask the analyst bot something..."></textarea>
  <button type="submit">Send</button>
</form>

{% if messages %}
<section class="messages">
  {% for m in messages %}
    <div class="message message-{{ m.role }}">
      <div class="role">{{ m.role }}</div>
      <div class="content">{{ m.content }}</div>
    </div>
  {% endfor %}
</section>
{% endif %}

{% if trace %}
<section class="trace">
  <h2>Tool trace</h2>
  {% for t in trace %}
    <div class="trace-entry trace-{{ t.decision }}">
      <span class="badge badge-{{ t.decision }}">{{ t.decision }}</span>
      <code class="tool">{{ t.tool }}</code>
      <span class="reason">{{ t.reason }}</span>
    </div>
  {% endfor %}
</section>
{% endif %}
{% endblock %}
```

---

### Task 2: Create `static/style.css`

**Files:**
- Create: `static/style.css`

- [ ] **Step 1: Create the stylesheet**

```css
:root {
  --bg: #fafafa;
  --fg: #1a1a1a;
  --muted: #666;
  --accent: #0066cc;
  --allow: #1a7f37;
  --deny: #cf222e;
  --error: #9a3412;
  --pending: #b07d00;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.5;
}

header {
  background: #fff;
  border-bottom: 1px solid #ddd;
  padding: 0.75rem 1.5rem;
}

header nav a {
  margin-right: 1rem;
  color: var(--accent);
  text-decoration: none;
}

main {
  max-width: 800px;
  margin: 1.5rem auto;
  padding: 0 1.5rem;
}

h1 { margin-top: 0; }

.chat-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
}

.chat-form textarea {
  width: 100%;
  padding: 0.5rem;
  font-family: inherit;
  font-size: 1rem;
  border: 1px solid #ccc;
  border-radius: 4px;
}

.chat-form button {
  align-self: flex-end;
  padding: 0.5rem 1.5rem;
  background: var(--accent);
  color: #fff;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.messages { margin-bottom: 1.5rem; }

.message {
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.5rem;
  background: #fff;
}

.message-user { border-left: 4px solid var(--accent); }
.message-assistant { border-left: 4px solid var(--muted); }

.message .role {
  text-transform: uppercase;
  font-size: 0.75rem;
  color: var(--muted);
  margin-bottom: 0.25rem;
}

.trace-entry {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
  margin-bottom: 0.25rem;
  font-size: 0.9rem;
  background: #fff;
  border: 1px solid #eee;
}

.badge {
  display: inline-block;
  padding: 0.1rem 0.5rem;
  border-radius: 10px;
  font-size: 0.75rem;
  text-transform: uppercase;
  color: #fff;
}

.badge-allow { background: var(--allow); }
.badge-deny { background: var(--deny); }
.badge-error { background: var(--error); }
.badge-pending_approval { background: var(--pending); }

.trace-entry code.tool {
  font-family: ui-monospace, Menlo, monospace;
  background: #f0f0f0;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
}

.trace-entry .reason {
  color: var(--muted);
  font-size: 0.85rem;
}
```

---

### Task 3: Modify `app.py` for templates + form-based POST

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Update `app.py` to mount templates, static, and handle form submissions**

Key changes:
- Add `from fastapi.staticfiles import StaticFiles` and `from fastapi.responses import HTMLResponse, JSONResponse` and `from fastapi.templating import Jinja2Templates` and `from starlette.requests import Request`
- `templates = Jinja2Templates(directory="templates")`
- `app.mount("/static", StaticFiles(directory="static"), name="static")`
- `GET /` → renders `chat.html` with empty messages/trace
- `POST /chat` → inspects Content-Type; form data → render template; JSON → return JSON
- The /chat endpoint also accepts either Content-Type

```python
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
from typing import Any

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
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Run the OpenAI tool-calling loop. Returns (final_messages, trace_dicts)."""
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
                return messages, [t.__dict__ for t in agent.trace]
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
                tool_content = (
                    str(result.value) if result.ok else (result.error or "")
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": tool_content}
                )
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
        return templates.TemplateResponse(
            request, "chat.html", {"messages": [], "trace": []}
        )

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
            messages_out, trace = _run_chat_loop(messages_in, cfg)
            last = messages_out[-1] if messages_out else {"content": ""}
            reply = last.get("content", "") if isinstance(last, dict) else ""
            return JSONResponse(
                {"reply": reply, "trace": trace, "messages": messages_out}
            )
        # form submission
        form = await request.form()
        message = form.get("message", "").strip() if form.get("message") else ""
        if not message:
            raise HTTPException(status_code=400, detail="message must be non-empty")
        display_messages: list[dict[str, str]] = [
            {"role": "user", "content": message}
        ]
        messages_in = [{"role": "user", "content": message}]
        messages_out, trace = _run_chat_loop(messages_in, cfg)
        last = messages_out[-1] if messages_out else {"content": ""}
        reply = last.get("content", "") if isinstance(last, dict) else ""
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

    @app.get("/audit")
    async def audit_view() -> dict[str, object]:
        return await api_audit()

    @app.get("/policy")
    async def policy_view() -> dict[str, str]:
        if not cfg.policy_path.exists():
            raise HTTPException(
                status_code=500, detail=f"policy file not found: {cfg.policy_path}"
            )
        return {"content": cfg.policy_path.read_text()}

    return app


app = create_app()
```

- [ ] **Step 2: Run new tests to verify they pass**

See Task 4 for new tests.

---

### Task 4: Write the failing tests (add to `tests/test_app.py`)

**Files:**
- Modify: `tests/test_app.py`

- [ ] **Step 1: Add 5 new tests for the HTML flow**

```python
# ---------- F9: Jinja2 chat UI ----------


def test_index_renders_chat_html(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    client = TestClient(create_app(make_config(tmp_path)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<form" in resp.text
    assert "ZTA Chat" in resp.text


def test_chat_form_post_returns_html_with_reply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            return_value=Response(200, json=_openai_completion(content="echo: hi"))
        )
        resp = client.post(
            "/chat",
            data={"message": "hi"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "hi" in resp.text
    assert "echo: hi" in resp.text


def test_chat_form_post_renders_trace_panel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            side_effect=[
                Response(200, json=_openai_completion(tool_calls=[("echo", {"message": "hi"})])),
                Response(200, json=_openai_completion(content="echo: hi")),
            ]
        )
        resp = client.post(
            "/chat",
            data={"message": "hi"},
            follow_redirects=False,
        )
    assert resp.status_code == 200
    assert "trace-entry" in resp.text
    assert "echo" in resp.text
    assert "allow" in resp.text


def test_chat_form_post_empty_message_returns_400(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    client = TestClient(create_app(make_config(tmp_path)))
    resp = client.post(
        "/chat",
        data={"message": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_chat_html_includes_base_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    client = TestClient(create_app(make_config(tmp_path)))
    resp = client.get("/")
    assert resp.status_code == 200
    # base.html has a <nav> with links to Chat/Audit/Policy
    assert "<nav>" in resp.text
    assert 'href="/audit"' in resp.text
    assert 'href="/policy"' in resp.text
```

- [ ] **Step 2: Run new tests to verify they pass after the `app.py` change**

Run: `.venv/bin/pytest tests/test_app.py -v -k "index_renders or form_post or html_includes"`
Expected: 5 pass.

---

### Task 5: Full verification

- [ ] **Step 1: Lint, format, typecheck, coverage**

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && \
  .venv/bin/mypy zta && .venv/bin/pytest --cov=zta -v
```

Expected: all green.

---

### Task 6: Commit, push, auto-merge

- [ ] **Step 1: Stage**

```bash
git add app.py templates/ static/ tests/test_app.py \
        docs/superpowers/plans/2026-06-18-f09-jinja-chat-ui.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat(f09): Jinja2 chat UI for /, form-based POST /chat

- templates/base.html: shared layout with nav (Chat/Audit/Policy)
- templates/chat.html: chat form + message list + inline tool trace
- static/style.css: minimal styles (allow/deny/error/pending badges)
- app.py: GET / renders chat.html; POST /chat inspects Content-Type
  (form -> HTML, JSON -> JSONResponse); static mount; Jinja2Templates
- 5 new tests for HTML flow"
```

- [ ] **Step 3: Push, create PR, auto-merge**

```bash
git push -u origin feature/f09-jinja-chat-ui
# PR + auto-merge via API as in F7/F8
```

---

## Self-Review

**Spec coverage:** Spec section 3.6 / spec section 3.7: chat page is the entry point. Form-based POST /chat with re-render. Inline tool trace. base.html + chat.html + style.css.

**Placeholders:** audit UI is F10; policy UI is F11. base.html already links to them but they return JSON for now (those endpoints get wrapped in F10/F11).

**Type consistency:** Same `ChatRequest` / `ChatResponse` for JSON mode. New form-based response is `TemplateResponse`.

**Security:** No change to auth/gating; the runtime still gates every tool call. The new endpoint accepts form data, but the OpenAI loop and ZTA policy are unchanged. CSRF is not added in MVP (single-user demo); a real deployment would need it.
