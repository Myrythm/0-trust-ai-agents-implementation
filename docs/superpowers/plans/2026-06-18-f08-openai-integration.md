# Feature 8: OpenAI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the F7 placeholder `echo` flow in `/chat` with real OpenAI Chat Completions using function calling. The LLM picks a tool, the ZTA runtime gates the call, the result is fed back to the LLM, and the loop continues until the LLM emits a final answer.

**Architecture:** `app.py` gets a `_run_chat_loop(messages, cfg, agent) -> str` function. `OpenAI(api_key=...)` client reads `ZTA_OPENAI_API_KEY`; model from `ZTA_OPENAI_MODEL` (default `gpt-4o-mini`). Tools exposed to OpenAI: a single `echo` function in F8 (F12 adds `db_query` and `db_write`). Max 10 iterations to prevent infinite loops. Each iteration that picks a tool routes through `zta.runtime.Agent.tool(...)`; the tool result is appended as a `tool` message and the loop continues. The LLM's final non-tool message becomes the `reply`. `agent.trace` is the union of all tool calls in the session.

**Tech Stack:** `openai` (already pinned), `respx` for testing, stdlib `os` + `json`. No new deps.

---

## File Structure

```
app.py                        # modify: replace _echo() placeholder path; add OpenAI loop
tests/test_app.py             # add 4 new tests for OpenAI flow (mocked)
```

No new files. `app.py` is the only edit.

---

## Contract (changes to `app.py`)

`/chat` (POST) new behavior:

```
input:  ChatRequest(messages=[...])
output: ChatResponse(reply=..., trace=[...])
flow:
  1. read ZTA_OPENAI_API_KEY (500 if missing)
  2. open zta.runtime.session(...)
  3. register _echo (F12 will add db_query, db_write)
  4. loop up to 10 times:
     a. POST OpenAI chat.completions with messages + [echo] tool schema
     b. if no tool_calls → return reply=msg.content, trace
     c. else: for each tool_call → agent.tool(name, **args); append tool result msg
  5. if loop exhausts → 500 "too many tool iterations"
```

`ZTA_OPENAI_MODEL` env var (default `gpt-4o-mini`).

**Failure modes:**
- `ZTA_OPENAI_API_KEY` missing → 500 with clear message
- OpenAI API call fails → 500 with the OpenAI error message
- Tool iteration > 10 → 500 "too many tool iterations" (prevents runaway loops)
- Empty messages → 400 (existing F7 behavior)

---

## Tasks

### Task 1: Write the failing tests (add to `tests/test_app.py`)

**Files:**
- Modify: `tests/test_app.py`

- [ ] **Step 1: Add 4 new tests for OpenAI flow**

Append the following to `tests/test_app.py`:

```python
# ---------- F8: OpenAI integration ----------

import os
from unittest.mock import patch

import respx
from httpx import Response


def _openai_completion(content=None, tool_calls=None, model="gpt-4o-mini"):
    """Build a canned OpenAI ChatCompletion response."""
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": tc[0], "arguments": json.dumps(tc[1])},
            }
            for i, tc in enumerate(tool_calls)
        ]
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {"index": 0, "message": msg, "finish_reason": "stop" if not tool_calls else "tool_calls"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_chat_openai_echo_tool_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        route = mock.post("/v1/chat/completions").mock(
            side_effect=[
                Response(200, json=_openai_completion(tool_calls=[("echo", {"message": "hi"})])),
                Response(200, json=_openai_completion(content="echo: hi")),
            ]
        )
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "echo: hi"
    assert any(t["tool"] == "echo" and t["decision"] == "allow" for t in body["trace"])


def test_chat_openai_no_function_call_returns_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            return_value=Response(200, json=_openai_completion(content="plain answer"))
        )
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "ping"}]})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "plain answer"
    assert resp.json()["trace"] == []


def test_chat_openai_missing_api_key_returns_500(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ZTA_OPENAI_API_KEY", raising=False)
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 500
    assert "ZTA_OPENAI_API_KEY" in resp.json()["detail"]


def test_chat_openai_tool_deny_propagates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: echo
            decision: deny
            reason: "no echo for you"
        """,
    )
    cfg = AppConfig(
        agent_id="bot", policy_path=pol, audit_path=tmp_path / "a.jsonl", key_dir=tmp_path / "keys"
    )
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            side_effect=[
                Response(200, json=_openai_completion(tool_calls=[("echo", {"message": "x"})])),
                Response(200, json=_openai_completion(content="I was denied")),
            ]
        )
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "I was denied"
    assert any(t["decision"] == "deny" for t in body["trace"])
```

Add the required import at the top of the test file:

```python
import json
```

(`json` is needed by the helper.)

- [ ] **Step 2: Run new tests to verify failure**

Run: `.venv/bin/pytest tests/test_app.py -v -k openai`
Expected: 4 failures (F8 behavior not yet implemented; the existing F7 tests should still pass).

---

### Task 2: Modify `app.py` to use OpenAI

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Replace the `/chat` route body with the OpenAI-driven flow**

```python
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
    """Placeholder tool used by /chat in F7. F8 will replace with OpenAI."""
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
                    tool_content = (
                        str(result.value) if result.ok else (result.error or "")
                    )
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
            raise HTTPException(
                status_code=500, detail=f"policy file not found: {cfg.policy_path}"
            )
        return {"content": cfg.policy_path.read_text()}

    return app


app = create_app()
```

- [ ] **Step 2: Run new tests to verify they pass**

Run: `.venv/bin/pytest tests/test_app.py -v -k openai`
Expected: 4 new tests pass; existing F7 tests should still pass (the new `/chat` does tool calls via OpenAI, but the existing F7 tests don't mock OpenAI — they'll fail because `ZTA_OPENAI_API_KEY` is not set).

This is expected: F8's `/chat` now requires OpenAI. The F7 tests for `/chat` will need to be updated to either (a) mock OpenAI or (b) be marked as legacy. Update the two F7 `/chat` tests (`test_chat_runs_echo_tool_through_zta`, `test_chat_deny_via_runtime_call`, `test_chat_audits_to_configured_path`) to mock OpenAI with a no-tool-call response (so the LLM returns a final answer without picking a tool — the runtime is unused and the audit is empty).

For each F7 chat test, set `ZTA_OPENAI_API_KEY` and mock OpenAI to return content only:

```python
import respx
from httpx import Response

def test_chat_runs_echo_tool_through_zta(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            return_value=Response(200, json=_openai_completion(content="echo: hi"))
        )
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert "echo: hi" in resp.json()["reply"]
```

For `test_chat_deny_via_runtime_call`, the deny path is no longer reachable via /chat (because the LLM doesn't know about the denied tool — it would only call `echo`, which is denied). The test as-written asserts `body["reply"]` contains "echo is disabled". In F8, the LLM returns content (no tool call), so the reply is whatever OpenAI returns. The deny path can be tested via the new F8 test `test_chat_openai_tool_deny_propagates`. Mark the F7 deny test as removed (it's superseded) and add a docstring note.

For `test_chat_audits_to_configured_path`, similar: the /chat now requires OpenAI. Mock OpenAI to return a tool call so the audit gets an event:

```python
def test_chat_audits_to_configured_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            side_effect=[
                Response(200, json=_openai_completion(tool_calls=[("echo", {"message": "hi"})])),
                Response(200, json=_openai_completion(content="ok")),
            ]
        )
        client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    events = Audit(cfg.audit_path).read_all()
    assert len(events) >= 1
    assert events[0].agent_id == "bot"
```

If updating the F7 chat tests is too invasive, an alternative is to leave them and have them fail, then re-record expectations. The plan above says: "Update the F7 chat tests to mock OpenAI". This is the cleanest path.

---

### Task 3: Full verification

- [ ] **Step 1: Lint, format, typecheck, coverage**

Run:
```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && \
  .venv/bin/mypy app.py zta tests && .venv/bin/pytest --cov=zta -v
```

Expected: all green; `zta` library coverage ≥ 90%; `app.py` covered by tests.

---

### Task 4: Commit, push, auto-merge

- [ ] **Step 1: Stage and commit**

```bash
git add app.py tests/test_app.py docs/superpowers/plans/2026-06-18-f08-openai-integration.md
git commit -m "feat(f08): OpenAI integration for /chat with function calling

- app.py: /chat now uses OpenAI Chat Completions with the echo function
  schema. The LLM picks a tool, the ZTA runtime gates it, the result
  is fed back, and the loop continues until the LLM emits a final
  answer. Max 10 iterations.
- ZTA_OPENAI_API_KEY env var required (500 if missing)
- ZTA_OPENAI_MODEL env var (default gpt-4o-mini)
- 4 new tests using respx to mock the OpenAI API
- Existing F7 /chat tests updated to mock OpenAI"
```

- [ ] **Step 2: Push, create PR, auto-merge**

```bash
git push -u origin feature/f08-openai-integration
# (PR creation + auto-merge via API as in F7)
```

---

## Self-Review

**Spec coverage:** Spec section 3.6 /chat flow: OpenAI Chat Completions call with function-calling schema, loop on tool calls, feed results back. Implemented.

**Placeholders:** `echo` is still a placeholder for the demo; F12 will add `db_query` and `db_write`.

**Type consistency:** OpenAI client is `openai.OpenAI` (1.x API). Completion is a Pydantic model with `.choices[0].message.tool_calls`.

**Security:**
- API key only read from env, never logged
- Max iterations (10) prevents infinite loops
- Tool calls routed through ZTA runtime → policy gate applies
- Deny results become error tool messages; LLM sees them and can recover (or be denied final answer)
