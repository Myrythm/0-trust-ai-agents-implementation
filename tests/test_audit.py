"""Tests for zta.audit — append-only JSONL log with SHA-256 hash chain."""

from __future__ import annotations

import json
from pathlib import Path

from zta.audit import GENESIS_HASH, Audit
from zta.errors import AuditError, ZTAError


def test_audit_init_creates_file_if_missing(tmp_path: Path) -> None:
    Audit(tmp_path / "audit.jsonl")
    assert (tmp_path / "audit.jsonl").exists()


def test_audit_init_creates_parent_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nest" / "audit.jsonl"
    Audit(nested)
    assert nested.parent.is_dir()


def test_append_returns_event_with_hashes(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    e = a.append(
        agent_id="a1",
        request_id="r1",
        action="tool.invoke:x",
        resource="res",
        decision="allow",
        reason="ok",
    )
    assert e.prev_hash == GENESIS_HASH
    assert e.this_hash
    assert len(e.this_hash) == 64


def test_append_assigns_event_id_and_ts(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    e = a.append(
        agent_id="a1", request_id="r1", action="x", resource="r", decision="allow", reason="ok"
    )
    assert e.event_id
    assert e.ts
    assert "T" in e.ts


def test_append_chains_hashes(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    e1 = a.append(
        agent_id="a1", request_id="r1", action="x", resource="r", decision="allow", reason="ok"
    )
    e2 = a.append(
        agent_id="a1", request_id="r2", action="x", resource="r", decision="allow", reason="ok"
    )
    assert e2.prev_hash == e1.this_hash


def test_read_all_returns_empty_list_for_new_audit(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    assert a.read_all() == []


def test_read_all_returns_appended_events_in_order(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    a.append(
        agent_id="a1", request_id="r1", action="x", resource="r", decision="allow", reason="ok"
    )
    a.append(agent_id="a1", request_id="r2", action="y", resource="r", decision="deny", reason="no")
    a.append(
        agent_id="a1", request_id="r3", action="z", resource="r", decision="allow", reason="ok"
    )
    events = a.read_all()
    assert [e.request_id for e in events] == ["r1", "r2", "r3"]
    assert [e.action for e in events] == ["x", "y", "z"]


def test_read_all_skips_malformed_lines(tmp_path: Path) -> None:
    p = tmp_path / "audit.jsonl"
    p.write_text("not valid json\n" + "{}\n" + "[]\n")
    a = Audit(p)
    assert a.read_all() == []


def test_verify_chain_returns_true_for_valid_chain(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    a.append(
        agent_id="a1", request_id="r1", action="x", resource="r", decision="allow", reason="ok"
    )
    a.append(agent_id="a1", request_id="r2", action="y", resource="r", decision="deny", reason="no")
    a.append(
        agent_id="a1", request_id="r3", action="z", resource="r", decision="allow", reason="ok"
    )
    assert a.verify_chain() is True


def test_verify_chain_detects_tampered_action(tmp_path: Path) -> None:
    p = tmp_path / "audit.jsonl"
    a = Audit(p)
    a.append(
        agent_id="a1", request_id="r1", action="x", resource="r", decision="allow", reason="ok"
    )
    a.append(agent_id="a1", request_id="r2", action="y", resource="r", decision="deny", reason="no")
    lines = p.read_text().splitlines()
    first = json.loads(lines[0])
    first["action"] = "TAMPERED"
    lines[0] = json.dumps(first)
    p.write_text("\n".join(lines) + "\n")
    assert Audit(p).verify_chain() is False


def test_verify_chain_detects_tampered_hash(tmp_path: Path) -> None:
    p = tmp_path / "audit.jsonl"
    a = Audit(p)
    a.append(
        agent_id="a1", request_id="r1", action="x", resource="r", decision="allow", reason="ok"
    )
    lines = p.read_text().splitlines()
    first = json.loads(lines[0])
    first["this_hash"] = "0" * 64
    lines[0] = json.dumps(first)
    p.write_text("\n".join(lines) + "\n")
    assert Audit(p).verify_chain() is False


def test_audit_exposes_no_update_or_delete_methods(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    forbidden = {"update", "delete", "remove", "clear", "truncate", "rewrite"}
    public = {n for n in vars(a) if not n.startswith("_")}
    assert public.isdisjoint(forbidden)


def test_audit_error_is_zta_error() -> None:
    assert issubclass(AuditError, ZTAError)
