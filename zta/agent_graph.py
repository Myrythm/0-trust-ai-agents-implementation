"""LangGraph integration for the ZTA runtime.

`build_zta_graph(model, agent, tools)` constructs a `StateGraph` whose nodes
mirror the current manual ReAct loop:

1. `call_model` — binds tools and invokes the chat model.
2. `zta_tools` — for each `AIMessage.tool_call`, routes through
   `agent.tool(...)` so policy enforcement and audit happen before execution.

The graph state carries `messages` and `trace`. Token-level streaming and
per-tool trace events are emitted via `astream_events(..., version="v2")`.
"""

from __future__ import annotations

import dataclasses
import logging
import operator
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.callbacks.manager import dispatch_custom_event
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from zta.runtime import Agent, TraceEntry

_log = logging.getLogger(__name__)


class AgentState(TypedDict):
    """Graph state: conversation messages + ZTA trace entries."""

    messages: Annotated[Sequence[BaseMessage], add_messages]
    trace: Annotated[list[TraceEntry], operator.add]


def _should_continue(state: AgentState) -> str:
    """Route from `call_model` to `zta_tools` if the model requested tools."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "zta_tools"
    return END


def build_zta_graph(
    model: BaseChatModel,
    agent: Agent,
    tools: Sequence[BaseTool],
) -> Any:
    """Build and compile a LangGraph agent that enforces ZTA policy per tool.

    Args:
        model: The chat model to use (e.g. ChatOpenAI). Must support tool
            calling via `bind_tools`.
        agent: The ZTA `Agent` handle that provides policy, audit, registry,
            and `agent.tool(...)` enforcement.
        tools: LangChain tools exposed to the model. Their names must match
            the tools registered on `agent.registry`.

    Returns:
        A compiled LangGraph application.
    """
    workflow = StateGraph(AgentState)

    async def call_model(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Invoke the model with tools bound."""
        response = await model.bind_tools(list(tools)).ainvoke(list(state["messages"]), config)
        return {"messages": [response]}

    async def zta_tools(state: AgentState) -> dict[str, Any]:
        """Execute each tool_call through the ZTA runtime."""
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage):
            return {"messages": [], "trace": []}

        tool_messages: list[ToolMessage] = []
        trace_start = len(agent.trace)

        for tool_call in last_message.tool_calls:
            name = tool_call["name"]
            args = tool_call.get("args", {})
            tool_call_id = tool_call["id"]

            result = agent.tool(name, **args)
            content = str(result.value) if result.ok else (result.error or "")
            tool_messages.append(ToolMessage(content=content, tool_call_id=tool_call_id))

            # agent.tool() appends the TraceEntry; emit it for streaming UIs.
            entry = agent.trace[-1]
            try:
                dispatch_custom_event("zta_trace", dataclasses.asdict(entry))
            except Exception:  # pragma: no cover - callbacks may not be present
                _log.debug("failed to dispatch zta_trace custom event", exc_info=True)

        _log.debug(
            "zta_tools node processed %d tool call(s); trace now has %d entries",
            len(tool_messages),
            len(agent.trace),
        )
        return {
            "messages": tool_messages,
            "trace": agent.trace[trace_start:],
        }

    workflow.add_node("call_model", call_model)
    workflow.add_node("zta_tools", zta_tools)
    workflow.add_edge(START, "call_model")
    workflow.add_conditional_edges(
        "call_model",
        _should_continue,
        {"zta_tools": "zta_tools", END: END},
    )
    workflow.add_edge("zta_tools", "call_model")
    return workflow.compile()
