"""Tests for zta.langgraph — LangGraph graph builder with ZTA enforcement."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from zta.agent_graph import AgentState, build_zta_graph
from zta.runtime import Agent, TraceEntry, session


def write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(dedent(body).lstrip())
    return p


class FakeChatModel(BaseChatModel):
    """Deterministic chat model for graph tests."""

    responses: list[BaseMessage]
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

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

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Ignore tool binding for the fake model."""
        return self


def _make_session(tmp_path: Path, policy_body: str) -> Agent:
    pol = write_policy(tmp_path, policy_body)
    ctx = session(
        agent="bot",
        policy=pol,
        audit=tmp_path / "audit.jsonl",
        key_dir=tmp_path / "keys",
    )
    return ctx.__enter__()


def test_build_graph_returns_compiled_graph(tmp_path: Path) -> None:
    agent = _make_session(tmp_path, "rules: []")

    @tool
    def noop() -> str:
        """Do nothing."""
        return "ok"

    model = FakeChatModel(responses=[AIMessage(content="hello")])
    graph = build_zta_graph(model, agent, [noop])
    assert graph is not None


@pytest.mark.anyio
async def test_graph_allows_allowed_tool_and_returns_result(tmp_path: Path) -> None:
    agent = _make_session(
        tmp_path,
        """
        rules:
          - tool: add
            decision: allow
        """,
    )
    agent.registry.register(lambda a, b: a + b, name="add")

    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "add", "args": {"a": 2, "b": 3}, "id": "call_1"}],
            ),
            AIMessage(content="The answer is 5"),
        ]
    )
    graph = build_zta_graph(model, agent, [add])
    result = await graph.ainvoke({"messages": [HumanMessage(content="add 2 and 3")]})

    messages = result["messages"]
    assert isinstance(messages[-1], AIMessage)
    assert "5" in messages[-1].content

    # Tool message should contain the result.
    tool_msg = messages[-2]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.content == "5"


@pytest.mark.anyio
async def test_graph_denies_denied_tool(tmp_path: Path) -> None:
    agent = _make_session(
        tmp_path,
        """
        rules:
          - tool: dangerous
            decision: deny
            reason: too risky
        """,
    )
    agent.registry.register(lambda: "should not run", name="dangerous")

    @tool
    def dangerous() -> str:
        """Do something dangerous."""
        return "should not run"

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "dangerous", "args": {}, "id": "call_deny"}],
            ),
            AIMessage(content="I cannot do that"),
        ]
    )
    graph = build_zta_graph(model, agent, [dangerous])
    result = await graph.ainvoke({"messages": [HumanMessage(content="run danger")]})

    tool_msg = result["messages"][-2]
    assert isinstance(tool_msg, ToolMessage)
    assert "too risky" in tool_msg.content

    trace = result["trace"]
    assert len(trace) == 1
    assert trace[0].decision == "deny"
    assert trace[0].tool == "dangerous"


@pytest.mark.anyio
async def test_graph_populates_trace(tmp_path: Path) -> None:
    agent = _make_session(
        tmp_path,
        """
        rules:
          - tool: echo
            decision: allow
        """,
    )
    agent.registry.register(lambda msg: f"echo: {msg}", name="echo")

    @tool
    def echo(msg: str) -> str:
        """Echo a message."""
        return f"echo: {msg}"

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"msg": "hi"}, "id": "call_echo"}],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_zta_graph(model, agent, [echo])
    result = await graph.ainvoke({"messages": [HumanMessage(content="echo hi")]})

    trace = result["trace"]
    assert len(trace) == 1
    assert isinstance(trace[0], TraceEntry)
    assert trace[0].tool == "echo"
    assert trace[0].decision == "allow"
    assert trace[0].ok is True


@pytest.mark.anyio
async def test_graph_handles_multiple_tool_calls(tmp_path: Path) -> None:
    agent = _make_session(
        tmp_path,
        """
        rules:
          - tool: a
            decision: allow
          - tool: b
            decision: allow
        """,
    )
    agent.registry.register(lambda: "A", name="a")
    agent.registry.register(lambda: "B", name="b")

    @tool
    def a() -> str:
        """Return A."""
        return "A"

    @tool
    def b() -> str:
        """Return B."""
        return "B"

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "a", "args": {}, "id": "call_a"},
                    {"name": "b", "args": {}, "id": "call_b"},
                ],
            ),
            AIMessage(content="got both"),
        ]
    )
    graph = build_zta_graph(model, agent, [a, b])
    result = await graph.ainvoke({"messages": [HumanMessage(content="call both")]})

    trace = result["trace"]
    assert len(trace) == 2
    assert {t.tool for t in trace} == {"a", "b"}


@pytest.mark.anyio
async def test_agent_trace_also_populated(tmp_path: Path) -> None:
    agent = _make_session(
        tmp_path,
        """
        rules:
          - tool: x
            decision: allow
        """,
    )
    agent.registry.register(lambda: "ok", name="x")

    @tool
    def x() -> str:
        """Return ok."""
        return "ok"

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "x", "args": {}, "id": "call_x"}],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_zta_graph(model, agent, [x])
    await graph.ainvoke({"messages": [HumanMessage(content="call x")]})

    assert len(agent.trace) == 1
    assert agent.trace[0].tool == "x"


def test_agent_state_is_typed_dict() -> None:
    from typing import is_typeddict

    assert is_typeddict(AgentState)
