"""Tests for zta.tools — @tool decorator + ToolRegistry."""

from __future__ import annotations

import pytest
from zta.errors import ToolError, ZTAError
from zta.tools import ToolRegistry, tool


def test_tool_decorator_marks_function() -> None:
    @tool
    def f(x: int) -> int:
        return x * 2

    assert f.__zta_tool_name__ == "f"


def test_tool_decorator_with_explicit_name() -> None:
    @tool("custom_name")
    def f(x: int) -> int:
        return x * 2

    assert f.__zta_tool_name__ == "custom_name"


def test_tool_decorator_function_still_callable() -> None:
    @tool
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_tool_decorator_preserves_metadata() -> None:
    @tool
    def my_func() -> int:
        """docstring here"""

        return 42

    assert my_func.__name__ == "my_func"
    assert my_func.__doc__ == "docstring here"


def test_registry_register_and_get() -> None:
    reg = ToolRegistry()

    @tool
    def hello(name: str) -> str:
        return f"hello {name}"

    reg.register(hello)
    assert reg.get("hello") is hello


def test_registry_register_with_explicit_name() -> None:
    reg = ToolRegistry()

    def fn() -> str:
        return "x"

    reg.register(fn, name="custom")
    assert reg.get("custom") is fn


def test_registry_register_twice_overwrites() -> None:
    reg = ToolRegistry()

    def a() -> str:
        return "a"

    def b() -> str:
        return "b"

    reg.register(a, name="f")
    reg.register(b, name="f")
    assert reg.get("f") is b


def test_registry_get_missing_raises_tool_error() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolError):
        reg.get("ghost")


def test_registry_list_returns_sorted_names() -> None:
    reg = ToolRegistry()

    @tool
    def zebra() -> None:
        return None

    @tool
    def apple() -> None:
        return None

    @tool
    def mango() -> None:
        return None

    reg.register(zebra)
    reg.register(apple)
    reg.register(mango)
    assert reg.list() == ["apple", "mango", "zebra"]


def test_registry_register_picks_up_decorator_name() -> None:
    reg = ToolRegistry()

    @tool("db_query")
    def q(sql: str) -> str:
        return sql

    reg.register(q)
    assert reg.get("db_query") is q


def test_tool_error_is_zta_error() -> None:
    assert issubclass(ToolError, ZTAError)
