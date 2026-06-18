"""Smoke test: the package imports and exposes a version string."""

from __future__ import annotations


def test_zta_imports() -> None:
    import zta

    assert zta.__version__ == "0.1.0"


def test_zta_version_is_string() -> None:
    import zta

    assert isinstance(zta.__version__, str)
    assert len(zta.__version__.split(".")) == 3
