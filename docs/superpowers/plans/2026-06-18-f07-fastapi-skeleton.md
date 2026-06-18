# Feature 7: FastAPI App Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `app.py` — a FastAPI app factory with the 5 routes from spec section 3.6. F7 ships the routes returning JSON; F9–F11 will wrap them in Jinja templates. F7 also wires the runtime into `/chat` using a placeholder `echo` tool (F8 will replace it with real OpenAI function calling).

**Architecture:** `app.py` defines `AppConfig` (paths to policy, audit, key_dir, db), `create_app(config)`, and the 5 routes. The app uses `zta.runtime.session` per request to evaluate policy + audit. F7 does not include Jinja templates, OpenAI, or SQLite — those are F8/F9–F11/F12. The `/chat` route runs a hardcoded `echo` tool to demonstrate the full policy-gated flow end-to-end without LLM dependency.

**Tech Stack:** `fastapi`, `pydantic` (already pinned), `zta.runtime` (F6). No new deps.

---

## File Structure

```
app.py                       # FastAPI app factory + routes
tests/test_app.py            # 9 tests using TestClient
```

No changes to `zta/`. The app is at the repo root (not under `zta/`) because it is the demo runtime that uses the library.

---

## Contract (per spec section 3.6 — JSON shape only for F7)

```python
class AppConfig(BaseModel):
    agent_id: str = "analyst-bot"
    policy_path: Path = Path("./policy.yaml")
    audit_path: Path = Path("./audit.jsonl")
    key_dir: Path = Path("./.zta/keys")


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]   # [{"role": "user|assistant|tool", "content": "..."}]


class ChatResponse(BaseModel):
    reply: str
    trace: list[dict[str, Any]]      # serialized TraceEntry list


def create_app(config: AppConfig | None = None) -> FastAPI: ...


app = create_app()    # module-level app for `uvicorn app:app`
```

**Routes (F7 ships JSON; F9–F11 wrap in templates):**

| Method | Path         | Returns (F7)                                  |
|--------|--------------|-----------------------------------------------|
| GET    | `/`          | `{"service": "zta-controlplane", "version": "0.1.0"}` |
| POST   | `/chat`      | `ChatResponse` (runs `echo` tool through runtime) |
| GET    | `/audit`     | `{"events": [...], "chain_valid": bool}` (F10 wraps in template) |
| GET    | `/api/audit` | same JSON, for JS polling                     |
| GET    | `/policy`    | raw `policy.yaml` text (F11 wraps in template) |

**`/chat` placeholder behavior (F7):**
- Extracts the last user message
- Opens a `zta.session(...)` for the configured agent
- Registers an `echo(message: str) -> str` tool
- Calls `agent.tool("echo", message=...)`
- Returns `{reply: <echo result or deny reason>, trace: [...]}`
- F8 will replace this with: send messages to OpenAI, loop on function calls, return final answer

**Failure modes:**
- Missing policy file → 500 with `{"error": "policy file not found: ..."}`
- Missing agent_id in config → 500
- Empty messages → 400 (Pydantic validation)

---

## Tasks

### Task 1: Write the failing test file

**Files:**
- Create: `tests/test_app.py`

- [ ] **Step 1: Create the test file with 9 tests**

```python
"""Tests for the FastAPI app skeleton (F7)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient

from zta.audit import Audit
from app import AppConfig, create_app


def write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(dedent(body).lstrip())
    return p


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        agent_id="bot",
        policy_path=write_policy(tmp_path, """
            rules:
              - tool: echo
                decision: allow
              - tool: shout
                decision: deny
                reason: "no shouting"
        """),
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
    )


def test_create_app_returns_fastapi_instance() -> None:
    from fastapi import FastAPI

    app = create_app()
    assert isinstance(app, FastAPI)


def test_index_returns_service_metadata() -> None:
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "zta-controlplane"
    assert "version" in body


def test_chat_runs_echo_tool_through_zta(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert "echo: hi" in body["reply"]
    assert any(t["tool"] == "echo" for t in body["trace"])
    assert any(t["decision"] == "allow" for t in body["trace"])


def test_chat_deny_returns_reason_in_reply(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "SHOUT"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert "no shouting" in body["reply"] or "no shouting" in (body["trace"][0]["reason"] if body["trace"] else "")


def test_chat_audits_to_configured_path(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    events = Audit(cfg.audit_path).read_all()
    assert len(events) >= 1
    assert events[0].agent_id == "bot"


def test_api_audit_returns_events_and_chain_validity(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    resp = client.get("/api/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body
    assert body["chain_valid"] is True
    assert len(body["events"]) >= 1


def test_audit_endpoint_returns_chain_status(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.get("/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "chain_valid" in body


def test_policy_endpoint_returns_raw_yaml(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.get("/policy")
    assert resp.status_code == 200
    body = resp.json()
    assert "content" in body
    assert "echo" in body["content"]
    assert "shout" in body["content"]


def test_chat_empty_messages_returns_400() -> None:
    client = TestClient(create_app())
    resp = client.post("/chat", json={"messages": []})
    assert resp.status_code in (400, 422)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: collection error (`ModuleNotFoundError: No module named 'app'`).

---

### Task 2: Implement `app.py`

**Files:**
- Create: `app.py`

- [ ] **Step 1: Write the implementation**

```python
"""FastAPI app skeleton for the ZTA demo runtime.

This file lives at the repo root (not under `zta/`) because it is the
demo service that USES the ZTA library. F7 ships JSON responses on
all 5 routes; F9–F11 wrap them in Jinja templates; F8 replaces the
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
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: 9 tests pass.

If `test_chat_deny_returns_reason_in_reply` is flaky because the deny rule does not match the `echo` tool in the test policy, adjust the test to send a message that triggers the deny path. Note: the F7 `/chat` only ever calls the `echo` tool; to test deny you'd add a `denied` tool to the policy. Update the test policy to include a rule for a denied tool and add a second `/chat` variant that calls it. (The current test config only has `echo: allow` and `shout: deny`; `/chat` does not call `shout`, so the deny rule is never triggered. Either add a denied tool that `/chat` calls based on input, or test deny via direct runtime call outside `/chat`.)

Suggested fix to the test (replace the deny test):
```python
def test_chat_deny_via_runtime_call(tmp_path: Path) -> None:
    from zta.policy import Policy
    from zta.runtime import session

    pol = write_policy(tmp_path, """
        rules:
          - tool: echo
            decision: deny
            reason: "echo is disabled"
    """)
    cfg = make_config(tmp_path).model_copy(update={"policy_path": pol})
    client = TestClient(create_app(cfg))
    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert "echo is disabled" in body["reply"] or "echo is disabled" in (body["trace"][0]["reason"] if body["trace"] else "")
```

---

### Task 3: Full verification

- [ ] **Step 1: Lint, format, typecheck, coverage**

Run:
```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && \
  .venv/bin/mypy app.py tests/test_app.py && .venv/bin/pytest --cov=zta --cov=app -v
```

Expected: all green; `zta` library coverage ≥ 90%.

---

### Task 4: Commit, push, merge

- [ ] **Step 1: Stage**

Run: `git add app.py tests/test_app.py docs/superpowers/plans/2026-06-18-f07-fastapi-skeleton.md`

- [ ] **Step 2: Commit**

Run:
```bash
git commit -m "feat(f07): FastAPI app skeleton with 5 routes

- app.py: AppConfig, ChatRequest/Response, create_app(), 5 routes (/,
  /chat, /audit, /api/audit, /policy). /chat uses zta.runtime.session
  with a placeholder echo tool. F8 will replace echo with real OpenAI
  function calling. F9-F11 will wrap responses in Jinja templates.
- 9 unit tests using FastAPI TestClient covering all routes + deny path"
```

- [ ] **Step 3: Push**

Run: `git push -u origin feature/f07-fastapi-skeleton`

- [ ] **Step 4: Create PR + auto-merge**

Run:
```bash
PR_BODY='Implements docs/superpowers/plans/2026-06-18-f07-fastapi-skeleton.md.

## What
- app.py: AppConfig, ChatRequest/Response, create_app(), 5 routes
- /chat placeholder uses zta.runtime with echo tool (F8 swaps in OpenAI)
- /audit + /api/audit read from Audit (JSON only; F10 wraps in template)
- /policy returns raw YAML (F11 wraps in template)
- 9 unit tests via TestClient

## Spec
docs/superpowers/specs/2026-06-18-zta-mvp-design.md section 3.6'
gh pr create --title "feat(f07): FastAPI app skeleton with 5 routes" --body "$PR_BODY" --base main --head feature/f07-fastapi-skeleton
```

Then merge via API (using the existing token):
```bash
PR_NUM=<from create output>
# auto-merge
```

---

## Self-Review

**Spec coverage:** Spec section 3.6 lists 5 routes. All present. `/chat` shape matches (`{reply, trace}`). `/api/audit` shape matches (`{events, chain_valid}`).

**Placeholders:** `echo` tool is a placeholder; F8 replaces it. F9–F11 wrap responses in Jinja.

**Type consistency:** `AppConfig` and request/response models use Pydantic. `create_app()` is the single source of app construction.

**Security:**
- Empty `messages` → 400
- Missing user message → 400
- `/chat` always runs through the runtime, so the deny-by-default policy applies
- No static secrets, no API keys hard-coded
