"""RBAC permission matrix: which role may use which pages and tools.

This is Layer 1 of the two-layer authorization model. It is loaded from a
YAML file (`roles.yaml`) and answers `tool_allowed(...)` / `page_allowed(...)`.
The policy engine (`zta.policy`) is Layer 2 and stays role-agnostic; the two
are composed with AND at the runtime surface.

Validation happens at load time: every role must be a known `Role` and every
referenced page/tool must be known, so typos fail loudly instead of silently
denying. An unknown role queried at runtime simply returns False
(deny-by-default).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypedDict

import yaml

from zta.errors import RbacError


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class RoleRow(TypedDict):
    """One row of the rendered permission matrix (see `Permissions.as_table`)."""

    role: str
    pages: list[str]
    tools: list[str]


KNOWN_PAGES: frozenset[str] = frozenset({"chat", "audit", "policy", "users", "roles"})
KNOWN_TOOLS: frozenset[str] = frozenset({"echo", "db_query", "db_write"})
KNOWN_ROLES: frozenset[str] = frozenset(r.value for r in Role)


@dataclass
class Permissions:
    """A loaded RBAC matrix: role -> allowed pages and tools."""

    _pages: dict[str, set[str]]
    _tools: dict[str, set[str]]

    @classmethod
    def load(cls, path: Path) -> Permissions:
        if not path.exists():
            raise RbacError(f"roles file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise RbacError(f"invalid YAML in roles file {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RbacError(f"roles file root must be a mapping: {path}")
        roles_raw = raw.get("roles", {})
        if not isinstance(roles_raw, dict):
            raise RbacError(f"'roles' must be a mapping: {path}")

        pages: dict[str, set[str]] = {}
        tools: dict[str, set[str]] = {}
        for role, spec in roles_raw.items():
            if role not in KNOWN_ROLES:
                raise RbacError(f"unknown role {role!r}; must be one of {sorted(KNOWN_ROLES)}")
            if not isinstance(spec, dict):
                raise RbacError(f"role {role!r} spec must be a mapping")
            role_pages = set(spec.get("pages", []) or [])
            role_tools = set(spec.get("tools", []) or [])
            unknown_pages = role_pages - KNOWN_PAGES
            if unknown_pages:
                raise RbacError(
                    f"role {role!r} references unknown page(s): {sorted(unknown_pages)}"
                )
            unknown_tools = role_tools - KNOWN_TOOLS
            if unknown_tools:
                raise RbacError(
                    f"role {role!r} references unknown tool(s): {sorted(unknown_tools)}"
                )
            pages[role] = role_pages
            tools[role] = role_tools
        return cls(_pages=pages, _tools=tools)

    def tool_allowed(self, role: str, tool: str) -> bool:
        return tool in self._tools.get(role, set())

    def page_allowed(self, role: str, page: str) -> bool:
        return page in self._pages.get(role, set())

    def roles(self) -> list[str]:
        return sorted(self._pages)

    def as_table(self) -> list[RoleRow]:
        return [
            RoleRow(
                role=role,
                pages=sorted(self._pages.get(role, set())),
                tools=sorted(self._tools.get(role, set())),
            )
            for role in self.roles()
        ]
