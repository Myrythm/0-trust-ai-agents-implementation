"""Tests for the FastAPI app (F7 + F8; F8 now uses LangChain ChatOpenAI)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
import respx
from app import AppConfig, create_app
from fastapi.testclient import TestClient
from httpx import Response
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from zta.audit import Audit


def write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(dedent(body).lstrip())
    return p


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        agent_id="bot",
        policy_path=write_policy(
            tmp_path,
            """
            rules:
              - tool: echo
                decision: allow
              - tool: shout
                decision: deny
                reason: "no shouting"
            """,
        ),
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
    )


def _openai_completion(
    content: str | None = None,
    tool_calls: list[tuple[str, dict[str, object]]] | None = None,
    model: str = "gpt-4o-mini",
) -> dict[str, object]:
    """Build a canned OpenAI ChatCompletion response (still valid for ChatOpenAI)."""
    msg: dict[str, object] = {"role": "assistant", "content": content}
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
            {
                "index": 0,
                "message": msg,
                "finish_reason": "stop" if not tool_calls else "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class FakeStreamingChatModel(BaseChatModel):
    """Deterministic streaming chat model for SSE tests."""

    responses: list[AIMessage]
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-streaming-chat-model"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self.responses[self.idx % len(self.responses)]
        self.idx += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop, run_manager, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        response = self.responses[self.idx % len(self.responses)]
        self.idx += 1
        content = response.content or ""
        for char in content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=char))
        if response.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_calls=response.tool_calls)
            )

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        return self


def test_create_app_returns_fastapi_instance() -> None:
    from fastapi import FastAPI

    app = create_app()
    assert isinstance(app, FastAPI)


def test_index_returns_service_metadata() -> None:
    """F9 changed / to return HTML; the service name is in the page title."""
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "ZTA Chat" in resp.text


def test_chat_runs_echo_tool_through_zta(tmp_path: Path, monkeypatch) -> None:
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
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert "echo: hi" in body["reply"]
    assert any(t["tool"] == "echo" for t in body["trace"])
    assert any(t["decision"] == "allow" for t in body["trace"])


def test_chat_deny_via_runtime_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: echo
            decision: deny
            reason: "echo is disabled"
        """,
    )
    cfg = AppConfig(
        agent_id="bot",
        policy_path=pol,
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
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
    reply_or_reason = body["reply"] + " " + (body["trace"][0]["reason"] if body["trace"] else "")
    assert "echo is disabled" in reply_or_reason


def test_chat_audits_to_configured_path(tmp_path: Path, monkeypatch) -> None:
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


def test_api_audit_returns_events_and_chain_validity(tmp_path: Path, monkeypatch) -> None:
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
    resp = client.get("/api/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body
    assert body["chain_valid"] is True
    assert len(body["events"]) >= 1


def test_audit_endpoint_returns_chain_status(tmp_path: Path) -> None:
    """F10 changed /audit to HTML; chain status is now in the page itself."""
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.get("/audit")
    assert resp.status_code == 200
    body = resp.text
    assert "chain_valid" not in body  # JSON key
    assert "Chain valid" in body  # HTML banner copy


def test_policy_page_renders_html_with_yaml(tmp_path: Path) -> None:
    """F11 changed /policy to HTML; F7's policy test is replaced."""
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.get("/policy")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "echo" in body
    assert "shout" in body
    assert "raw-yaml" in body


# ---------- F11: Policy UI ----------


def test_policy_page_renders_html(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.get("/policy")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Policy" in resp.text


def test_policy_page_shows_default(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        default: deny
        rules:
          - tool: x
            decision: allow
        """,
    )
    cfg = AppConfig(
        agent_id="bot", policy_path=pol, audit_path=tmp_path / "a.jsonl", key_dir=tmp_path / "keys"
    )
    client = TestClient(create_app(cfg))
    resp = client.get("/policy")
    assert resp.status_code == 200
    assert "deny" in resp.text


def test_policy_page_shows_agent_scope(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        agent: analyst-bot
        rules:
          - tool: x
            decision: allow
        """,
    )
    cfg = AppConfig(
        agent_id="bot", policy_path=pol, audit_path=tmp_path / "a.jsonl", key_dir=tmp_path / "keys"
    )
    client = TestClient(create_app(cfg))
    resp = client.get("/policy")
    assert resp.status_code == 200
    assert "analyst-bot" in resp.text


def test_policy_page_missing_file_returns_500(tmp_path: Path) -> None:
    cfg = AppConfig(
        agent_id="bot",
        policy_path=tmp_path / "missing.yaml",
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
    )
    client = TestClient(create_app(cfg))
    resp = client.get("/policy")
    assert resp.status_code == 500
    assert "policy file not found" in resp.text


def test_chat_empty_messages_returns_400() -> None:
    client = TestClient(create_app())
    resp = client.post("/chat", json={"messages": []})
    assert resp.status_code in (400, 422)


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


# ---------- F9: Jinja2 chat UI ----------


def test_index_renders_chat_html(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    client = TestClient(create_app(make_config(tmp_path)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<form" in resp.text
    assert "ZTA Chat" in resp.text
    assert 'id="chat-form"' in resp.text
    assert 'id="chat-messages"' in resp.text
    assert 'id="chat-trace"' in resp.text


def test_chat_html_includes_base_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    client = TestClient(create_app(make_config(tmp_path)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<nav>" in resp.text
    assert 'href="/audit"' in resp.text
    assert 'href="/policy"' in resp.text


# ---------- F10: Audit UI ----------


def test_audit_page_renders_html(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.get("/audit")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Audit log" in resp.text


def test_audit_page_shows_empty_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.get("/audit")
    assert resp.status_code == 200
    assert "No events yet" in resp.text


def test_audit_page_lists_events(tmp_path: Path, monkeypatch) -> None:
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
    resp = client.get("/audit")
    assert resp.status_code == 200
    body = resp.text
    assert "audit-table" in body
    assert "bot" in body
    assert "tool:echo" in body
    assert "allow" in body


def test_api_audit_still_returns_json_after_f10(tmp_path: Path, monkeypatch) -> None:
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
    resp = client.get("/api/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body
    assert body["chain_valid"] is True


# ---------- F12: Demo end-to-end ----------


def test_e2e_demo_seed_creates_tables(tmp_path) -> None:
    """examples/seed_db.py creates customers + orders tables with rows."""
    import os
    import sqlite3
    import subprocess
    import sys

    db_path = tmp_path / "demo.db"
    env = os.environ.copy()
    env["ZTA_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, "examples/seed_db.py"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(str(db_path))
    customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert customers == 5
    assert orders == 10
    db_path.unlink()


def test_e2e_chat_db_query_allowed(tmp_path, monkeypatch) -> None:
    """Full flow: chat with db_query (SELECT) is allowed, audit shows allow."""
    import sqlite3

    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ZTA_DB_PATH", str(tmp_path / "demo.db"))
    seed_db = tmp_path / "demo.db"
    conn = sqlite3.connect(str(seed_db))
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.executemany(
        "INSERT INTO customers VALUES (?, ?)", [(1, "Alice"), (2, "Bob"), (3, "Carol")]
    )
    conn.commit()
    conn.close()
    cfg = AppConfig(
        agent_id="analyst-bot",
        policy_path=Path("policy.yaml"),
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
    )
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            side_effect=[
                Response(
                    200,
                    json=_openai_completion(
                        tool_calls=[("db_query", {"sql": "SELECT * FROM customers"})]
                    ),
                ),
                Response(200, json=_openai_completion(content="3 customers: Alice, Bob, Carol")),
            ]
        )
        resp = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "show all customers"}]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "3 customers" in body["reply"]
    assert any(t["tool"] == "db_query" and t["decision"] == "allow" for t in body["trace"])


def test_e2e_chat_db_write_denied(tmp_path, monkeypatch) -> None:
    """Full flow: chat with db_write is denied by policy, audit shows deny."""
    import sqlite3

    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ZTA_DB_PATH", str(tmp_path / "demo.db"))
    seed_db = tmp_path / "demo.db"
    conn = sqlite3.connect(str(seed_db))
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.close()
    cfg = AppConfig(
        agent_id="analyst-bot",
        policy_path=Path("policy.yaml"),
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
    )
    client = TestClient(create_app(cfg))
    with respx.mock(base_url="https://api.openai.com") as mock:
        mock.post("/v1/chat/completions").mock(
            side_effect=[
                Response(
                    200,
                    json=_openai_completion(
                        tool_calls=[("db_write", {"sql": "DELETE FROM customers"})]
                    ),
                ),
                Response(200, json=_openai_completion(content="I cannot write to the database")),
            ]
        )
        resp = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "delete all customers"}]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "I cannot write to the database"
    assert any(t["tool"] == "db_write" and t["decision"] == "deny" for t in body["trace"])


# ---------- Streaming endpoint ----------


def _parse_sse(resp_text: str) -> list[tuple[str, dict[str, object]]]:
    """Parse raw SSE response text into (event, data) pairs."""
    events: list[tuple[str, dict[str, object]]] = []
    current_event: str | None = None
    for line in resp_text.splitlines():
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: ") and current_event is not None:
            payload = json.loads(line[len("data: ") :])
            events.append((current_event, payload))
            current_event = None
    return events


def test_chat_stream_returns_sse(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    fake_model = FakeStreamingChatModel(responses=[AIMessage(content="hello")])
    monkeypatch.setattr("app._get_chat_model", lambda: fake_model)
    resp = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    assert any(e == "end" for e, _ in events)


def test_chat_stream_emits_tokens_and_trace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    fake_model = FakeStreamingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"message": "hi"}, "id": "call_1"}],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr("app._get_chat_model", lambda: fake_model)
    resp = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    event_types = [e for e, _ in events]
    assert "token" in event_types
    assert "trace" in event_types
    trace_events = [data for e, data in events if e == "trace"]
    assert any(data["tool"] == "echo" and data["decision"] == "allow" for data in trace_events)


def test_chat_stream_empty_messages_returns_400(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZTA_OPENAI_API_KEY", "sk-test")
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.post("/chat/stream", json={"messages": []})
    assert resp.status_code in (400, 422)


@pytest.mark.anyio
async def test_chat_stream_missing_api_key_returns_error_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ZTA_OPENAI_API_KEY", raising=False)
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    resp = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert "error" in resp.text
