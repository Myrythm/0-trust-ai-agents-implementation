"""Tests for the FastAPI app (F7 + F8)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import respx
from app import AppConfig, create_app
from fastapi.testclient import TestClient
from httpx import Response
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
            {
                "index": 0,
                "message": msg,
                "finish_reason": "stop" if not tool_calls else "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


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
