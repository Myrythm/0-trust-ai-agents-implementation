"""The thin runtime API: session() + Agent.

`session(agent, policy, audit, key_dir)` loads identity, policy, and
audit, creates an empty tool registry, and yields an `Agent`. The
caller registers tools on `agent.registry`, then calls
`agent.tool(name, **args)`. Every call routes through policy, executes
(if allowed), and writes an audit event. `agent.trace` is populated
for UI display.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from zta.audit import Audit
from zta.identity import Identity
from zta.policy import Decision, Policy
from zta.rbac import Permissions
from zta.tools import ToolRegistry

_log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """The outcome of one `agent.tool(...)` call."""

    ok: bool
    value: Any | None = None
    error: str | None = None


@dataclass
class TraceEntry:
    """One entry in `agent.trace`, populated per `tool()` call."""

    ts: str
    request_id: str
    tool: str
    args: dict[str, Any]
    decision: str
    reason: str
    ok: bool
    error: str | None


@dataclass
class Agent:
    """The runtime handle inside a session(). Owns identity, policy, audit, registry."""

    agent_id: str
    policy: Policy
    audit: Audit
    identity: Identity
    registry: ToolRegistry
    user: str = ""
    role: str = ""
    permissions: Permissions | None = None
    trace: list[TraceEntry] = field(default_factory=list)

    def tool(self, name: str, **args: Any) -> ToolResult:
        request_id = uuid.uuid4().hex
        ts = datetime.now(UTC).isoformat()

        # Layer 1 (RBAC): is this role allowed to use this tool at all?
        # Skipped when no permissions matrix is configured (library default).
        if self.permissions is not None and not self.permissions.tool_allowed(self.role, name):
            reason = f"rbac: role {self.role!r} not permitted to use {name!r}"
            return self._record_deny(
                request_id=request_id, ts=ts, name=name, args=args, reason=reason
            )

        # Layer 2 (policy): is this specific call safe?
        decision = self.policy.decide(agent_id=self.agent_id, tool=name, args=args)
        reason = self.policy.reason()

        if decision is Decision.DENY:
            return self._record_deny(
                request_id=request_id, ts=ts, name=name, args=args, reason=reason
            )
        if decision is Decision.PENDING_APPROVAL:
            full_reason = f"{reason} (pending_approval denied in MVP)"
            return self._record_pending(
                request_id=request_id, ts=ts, name=name, args=args, reason=full_reason
            )

        try:
            fn = self.registry.get(name)
        except Exception as exc:
            return self._record_error(
                request_id=request_id, ts=ts, name=name, args=args, error_msg=str(exc)
            )
        try:
            value = fn.invoke(args) if isinstance(fn, BaseTool) else fn(**args)
        except Exception as exc:
            return self._record_error(
                request_id=request_id, ts=ts, name=name, args=args, error_msg=str(exc)
            )
        return self._record_allow(
            request_id=request_id, ts=ts, name=name, args=args, reason=reason, value=value
        )

    def _record_allow(
        self,
        *,
        request_id: str,
        ts: str,
        name: str,
        args: dict[str, Any],
        reason: str,
        value: Any,
    ) -> ToolResult:
        self.audit.append(
            agent_id=self.agent_id,
            request_id=request_id,
            action=f"tool:{name}",
            resource=f"tool:{name}",
            decision="allow",
            reason=reason,
            user=self.user,
        )
        entry = TraceEntry(
            ts=ts,
            request_id=request_id,
            tool=name,
            args=args,
            decision="allow",
            reason=reason,
            ok=True,
            error=None,
        )
        self.trace.append(entry)
        return ToolResult(ok=True, value=value, error=None)

    def _record_deny(
        self,
        *,
        request_id: str,
        ts: str,
        name: str,
        args: dict[str, Any],
        reason: str,
    ) -> ToolResult:
        self.audit.append(
            agent_id=self.agent_id,
            request_id=request_id,
            action=f"tool:{name}",
            resource=f"tool:{name}",
            decision="deny",
            reason=reason,
            user=self.user,
        )
        entry = TraceEntry(
            ts=ts,
            request_id=request_id,
            tool=name,
            args=args,
            decision="deny",
            reason=reason,
            ok=False,
            error=reason,
        )
        self.trace.append(entry)
        return ToolResult(ok=False, value=None, error=reason)

    def _record_pending(
        self,
        *,
        request_id: str,
        ts: str,
        name: str,
        args: dict[str, Any],
        reason: str,
    ) -> ToolResult:
        self.audit.append(
            agent_id=self.agent_id,
            request_id=request_id,
            action=f"tool:{name}",
            resource=f"tool:{name}",
            decision="pending_approval",
            reason=reason,
            user=self.user,
        )
        entry = TraceEntry(
            ts=ts,
            request_id=request_id,
            tool=name,
            args=args,
            decision="pending_approval",
            reason=reason,
            ok=False,
            error=reason,
        )
        self.trace.append(entry)
        return ToolResult(ok=False, value=None, error=reason)

    def _record_error(
        self,
        *,
        request_id: str,
        ts: str,
        name: str,
        args: dict[str, Any],
        error_msg: str,
    ) -> ToolResult:
        self.audit.append(
            agent_id=self.agent_id,
            request_id=request_id,
            action=f"tool:{name}",
            resource=f"tool:{name}",
            decision="error",
            reason=error_msg,
            user=self.user,
        )
        entry = TraceEntry(
            ts=ts,
            request_id=request_id,
            tool=name,
            args=args,
            decision="error",
            reason=error_msg,
            ok=False,
            error=error_msg,
        )
        self.trace.append(entry)
        return ToolResult(ok=False, value=None, error=error_msg)


@contextmanager
def session(
    *,
    agent: str,
    policy: Path,
    audit: Path,
    key_dir: Path,
    user: str = "",
    role: str = "",
    permissions: Permissions | None = None,
) -> Iterator[Agent]:
    """Yield an `Agent` bound to the given identity/policy/audit/key_dir.

    `user`/`role`/`permissions` enable the Layer-1 RBAC check and user-attributed
    audit; when `permissions` is None the RBAC layer is skipped (policy still runs).
    """
    identity = Identity.load_or_create(agent, key_dir)
    loaded_policy = Policy.load(policy)
    audit_log = Audit(audit)
    yield Agent(
        agent_id=agent,
        policy=loaded_policy,
        audit=audit_log,
        identity=identity,
        registry=ToolRegistry(),
        user=user,
        role=role,
        permissions=permissions,
    )
