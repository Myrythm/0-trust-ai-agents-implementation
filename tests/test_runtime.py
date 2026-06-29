"""Tests for zta.runtime — session() context manager + Agent."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from zta.audit import Audit
from zta.identity import Identity
from zta.policy import Policy
from zta.runtime import Agent, ToolResult, session
from zta.tools import ToolRegistry


def write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(dedent(body).lstrip())
    return p


def write_key_dir(tmp_path: Path) -> Path:
    return tmp_path / "keys"


def test_session_creates_identity(tmp_path: Path) -> None:
    pol = write_policy(tmp_path, "rules: []")
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        assert isinstance(a.identity, Identity)
    assert (write_key_dir(tmp_path) / "bot.pem").is_file()


def test_session_yields_agent_with_agent_id(tmp_path: Path) -> None:
    pol = write_policy(tmp_path, "rules: []")
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        assert isinstance(a, Agent)
        assert a.agent_id == "bot"


def test_session_loads_policy(tmp_path: Path) -> None:
    pol = write_policy(tmp_path, "rules: []")
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        assert isinstance(a.policy, Policy)


def test_session_creates_audit(tmp_path: Path) -> None:
    pol = write_policy(tmp_path, "rules: []")
    audit_path = tmp_path / "a.jsonl"
    with session(agent="bot", policy=pol, audit=audit_path, key_dir=write_key_dir(tmp_path)) as a:
        assert isinstance(a.audit, Audit)
    assert audit_path.exists()


def test_session_registry_is_empty_by_default(tmp_path: Path) -> None:
    pol = write_policy(tmp_path, "rules: []")
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        assert isinstance(a.registry, ToolRegistry)
        assert a.registry.list() == []


def test_session_uses_existing_key(tmp_path: Path) -> None:
    pol = write_policy(tmp_path, "rules: []")
    kd = write_key_dir(tmp_path)
    with session(agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=kd) as a1:
        first_pub = a1.identity.public_key_b64
    with session(agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=kd) as a2:
        assert a2.identity.public_key_b64 == first_pub


def test_agent_tool_allow_executes_and_audits(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: add
            decision: allow
    """,
    )
    audit_path = tmp_path / "a.jsonl"
    with session(agent="bot", policy=pol, audit=audit_path, key_dir=write_key_dir(tmp_path)) as a:
        a.registry.register(lambda a, b: a + b, name="add")
        result = a.tool("add", a=2, b=3)
    assert result.ok is True
    assert result.value == 5
    assert result.error is None
    events = Audit(audit_path).read_all()
    assert len(events) == 1
    assert events[0].decision == "allow"
    assert events[0].action == "tool:add"


def test_agent_tool_deny_does_not_execute_and_audits(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: dangerous
            decision: deny
            reason: "too risky"
    """,
    )
    audit_path = tmp_path / "a.jsonl"

    called = {"n": 0}

    def boom() -> str:
        called["n"] += 1
        return "should not happen"

    with session(agent="bot", policy=pol, audit=audit_path, key_dir=write_key_dir(tmp_path)) as a:
        a.registry.register(boom, name="dangerous")
        result = a.tool("dangerous")
    assert result.ok is False
    assert "too risky" in (result.error or "")
    assert called["n"] == 0
    events = Audit(audit_path).read_all()
    assert len(events) == 1
    assert events[0].decision == "deny"


def test_agent_tool_unknown_tool_returns_error_and_audits(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: missing
            decision: allow
    """,
    )
    audit_path = tmp_path / "a.jsonl"
    with session(agent="bot", policy=pol, audit=audit_path, key_dir=write_key_dir(tmp_path)) as a:
        result = a.tool("missing")
    assert result.ok is False
    assert "not registered" in (result.error or "")
    events = Audit(audit_path).read_all()
    assert len(events) == 1
    assert events[0].decision == "error"


def test_agent_tool_pending_approval_denies_in_mvp(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: risky
            decision: pending_approval
            reason: "needs human"
    """,
    )
    audit_path = tmp_path / "a.jsonl"
    with session(agent="bot", policy=pol, audit=audit_path, key_dir=write_key_dir(tmp_path)) as a:
        a.registry.register(lambda: "x", name="risky")
        result = a.tool("risky")
    assert result.ok is False
    assert "pending_approval" in (result.error or "")
    events = Audit(audit_path).read_all()
    assert events[0].decision == "pending_approval"


def test_agent_tool_exception_returns_error_and_audits(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: kaboom
            decision: allow
    """,
    )
    audit_path = tmp_path / "a.jsonl"

    def kaboom() -> str:
        raise ValueError("nope")

    with session(agent="bot", policy=pol, audit=audit_path, key_dir=write_key_dir(tmp_path)) as a:
        a.registry.register(kaboom, name="kaboom")
        result = a.tool("kaboom")
    assert result.ok is False
    assert "nope" in (result.error or "")
    events = Audit(audit_path).read_all()
    assert events[0].decision == "error"


def test_agent_trace_populated_by_tool_calls(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: t1
            decision: allow
          - tool: t2
            decision: deny
            reason: "no"
    """,
    )
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        a.registry.register(lambda: "ok", name="t1")
        a.tool("t1")
        a.tool("t2")
    assert len(a.trace) == 2
    assert a.trace[0].tool == "t1"
    assert a.trace[0].decision == "allow"
    assert a.trace[1].tool == "t2"
    assert a.trace[1].decision == "deny"


def test_agent_tool_assigns_request_id(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: x
            decision: allow
    """,
    )
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        a.registry.register(lambda: "ok", name="x")
        a.tool("x")
    assert a.trace[0].request_id
    assert len(a.trace[0].request_id) == 32


def test_request_id_propagates_to_audit_event(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: x
            decision: allow
    """,
    )
    audit_path = tmp_path / "a.jsonl"
    with session(agent="bot", policy=pol, audit=audit_path, key_dir=write_key_dir(tmp_path)) as a:
        a.registry.register(lambda: None, name="x")
        a.tool("x")
    events = Audit(audit_path).read_all()
    assert events[0].request_id == a.trace[0].request_id


def test_tool_result_shape_on_allow(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: x
            decision: allow
    """,
    )
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        a.registry.register(lambda: 42, name="x")
        r = a.tool("x")
    assert isinstance(r, ToolResult)
    assert r.ok is True
    assert r.value == 42
    assert r.error is None


def test_tool_result_shape_on_deny(tmp_path: Path) -> None:
    pol = write_policy(
        tmp_path,
        """
        rules:
          - tool: x
            decision: deny
            reason: "nope"
    """,
    )
    with session(
        agent="bot", policy=pol, audit=tmp_path / "a.jsonl", key_dir=write_key_dir(tmp_path)
    ) as a:
        r = a.tool("x")
    assert isinstance(r, ToolResult)
    assert r.ok is False
    assert r.value is None
    assert r.error == "nope"


# ---------- Layer-1 RBAC enforcement (Task 5) ----------


def _rbac_perms(tmp_path: Path):
    from zta.rbac import Permissions

    p = tmp_path / "roles.yaml"
    p.write_text(
        "roles:\n"
        '  manager:\n    pages: [chat]\n    tools: [echo, db_query, db_write]\n    tables: "*"\n'
        "  catalog:\n    pages: [chat]\n    tools: [echo, db_query]\n    tables: [Artist]\n"
    )
    return Permissions.load(p)


def _echo_policy(tmp_path: Path) -> Path:
    return write_policy(
        tmp_path,
        """
        default: deny
        rules:
          - tool: echo
            decision: allow
        """,
    )


def test_rbac_denies_tool_not_in_role(tmp_path: Path) -> None:
    """catalog has no db_write tool -> Layer-1 deny before policy runs."""
    with session(
        agent="bot",
        policy=_echo_policy(tmp_path),
        audit=tmp_path / "a.jsonl",
        key_dir=write_key_dir(tmp_path),
        user="cara",
        role="catalog",
        permissions=_rbac_perms(tmp_path),
    ) as a:
        a.registry.register(lambda message: f"echo: {message}", name="echo")
        result = a.tool("db_write", sql="DROP TABLE Track")
    assert result.ok is False
    assert "rbac" in (result.error or "").lower()
    assert a.trace[-1].decision == "deny"


def test_rbac_allows_then_policy_executes(tmp_path: Path) -> None:
    with session(
        agent="bot",
        policy=_echo_policy(tmp_path),
        audit=tmp_path / "a.jsonl",
        key_dir=write_key_dir(tmp_path),
        user="cara",
        role="catalog",
        permissions=_rbac_perms(tmp_path),
    ) as a:
        a.registry.register(lambda message: f"echo: {message}", name="echo")
        result = a.tool("echo", message="hi")
    assert result.ok is True
    assert result.value == "echo: hi"


def test_audit_records_user(tmp_path: Path) -> None:
    audit_path = tmp_path / "a.jsonl"
    with session(
        agent="bot",
        policy=_echo_policy(tmp_path),
        audit=audit_path,
        key_dir=write_key_dir(tmp_path),
        user="cara",
        role="catalog",
        permissions=_rbac_perms(tmp_path),
    ) as a:
        a.registry.register(lambda message: f"echo: {message}", name="echo")
        a.tool("echo", message="hi")
    assert Audit(audit_path).read_all()[-1].user == "cara"


def test_no_permissions_skips_rbac(tmp_path: Path) -> None:
    """With no permissions matrix, the RBAC layer is skipped (policy still runs)."""
    with session(
        agent="bot",
        policy=_echo_policy(tmp_path),
        audit=tmp_path / "a.jsonl",
        key_dir=write_key_dir(tmp_path),
        role="catalog",
    ) as a:
        a.registry.register(lambda message: f"echo: {message}", name="echo")
        result = a.tool("echo", message="hi")
    assert result.ok is True
    assert result.value == "echo: hi"


def test_tool_rbac_error_recorded_as_deny(tmp_path: Path) -> None:
    """A tool raising RbacError (e.g. table-scope deny) is recorded as deny, not error."""
    from zta.errors import RbacError

    def denier(message: str) -> str:
        raise RbacError("rbac: role 'catalog' not permitted to read table 'Employee'")

    with session(
        agent="bot",
        policy=_echo_policy(tmp_path),
        audit=tmp_path / "a.jsonl",
        key_dir=write_key_dir(tmp_path),
    ) as a:
        a.registry.register(denier, name="echo")
        result = a.tool("echo", message="hi")
    assert result.ok is False
    assert a.trace[-1].decision == "deny"
    assert "rbac" in (result.error or "")
