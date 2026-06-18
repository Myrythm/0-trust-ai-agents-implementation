"""Shared pytest fixtures for the ZTA MVP."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "zta"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ZTA_ENV", "test")
    monkeypatch.setenv("ZTA_LOG_LEVEL", "WARNING")
    yield  # noqa: PT022 - monkeypatch teardown is implicit
