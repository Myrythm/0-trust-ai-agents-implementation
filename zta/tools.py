"""Tool marker + registry.

`@tool` decorates a function to mark it as a ZTA tool and stamps the
chosen name on the wrapper. `ToolRegistry` is a thin in-process map for
storing and looking up tools by name. This module is the marker +
lookup; policy enforcement lives in `zta.runtime` (F6).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, overload

from zta.errors import ToolError

F = TypeVar("F", bound=Callable[..., Any])

_TOOL_NAME_ATTR = "__zta_tool_name__"


@overload
def tool(fn: F, /) -> Any: ...
@overload
def tool(name: str = ..., /) -> Callable[[F], Any]: ...
def tool(name: str | F | None = None) -> Callable[[F], Any] | Any:
    """Mark a function as a ZTA tool.

    Usage:
        @tool                 # bare: name = fn.__name__
        def db_query(sql): ...

        @tool()               # no name override
        def db_query(sql): ...

        @tool("db.query")     # explicit name
        def db_query(sql): ...
    """
    if callable(name):
        return _make_wrapper(name.__name__, name)
    chosen = name

    def decorator(fn: F) -> Any:
        return _make_wrapper(chosen if isinstance(chosen, str) else fn.__name__, fn)

    return decorator


def _make_wrapper(tool_name: str, fn: F) -> Any:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    setattr(wrapper, _TOOL_NAME_ATTR, tool_name)
    return wrapper


class ToolRegistry:
    """In-process map of tool name → callable."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, fn: Callable[..., Any], *, name: str | None = None) -> None:
        """Register `fn` under the chosen name (or its tool/function name)."""
        chosen = name if name is not None else getattr(fn, _TOOL_NAME_ATTR, None) or fn.__name__
        self._tools[chosen] = fn

    def get(self, name: str) -> Callable[..., Any]:
        """Return the tool registered as `name`; raise ToolError if missing."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"tool not registered: {name!r}") from exc

    def list(self) -> list[str]:
        """Return the registered tool names in sorted order."""
        return sorted(self._tools)
