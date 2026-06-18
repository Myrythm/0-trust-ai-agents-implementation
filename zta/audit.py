"""Append-only audit log with SHA-256 hash chain.

Every state-changing action across the control plane calls
`Audit.append(...)`. Events are stored as one JSON object per line in
a JSONL file. Each event carries `prev_hash` (the predecessor's
`this_hash`, or `GENESIS_HASH` for the first) and `this_hash` =
`sha256(prev_hash || canonical(event_without_this_hash))`. Tampering
with any line breaks the chain at `verify_chain()`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GENESIS_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> bytes:
    """Stable byte representation used for hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class AuditEvent(BaseModel):
    """One audit event as stored in the JSONL log."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    agent_id: str
    request_id: str
    action: str
    resource: str
    decision: str
    reason: str
    prev_hash: str = GENESIS_HASH
    this_hash: str = ""

    def compute_this_hash(self) -> str:
        body = self.model_dump(mode="json", exclude={"this_hash"})
        return hashlib.sha256(self.prev_hash.encode("utf-8") + _canonical(body)).hexdigest()


class Audit:
    """Append-only JSONL audit log with hash-chained integrity."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()

    def append(
        self,
        *,
        agent_id: str,
        request_id: str,
        action: str,
        resource: str,
        decision: str,
        reason: str,
    ) -> AuditEvent:
        prev_hash = self._last_hash()
        event = AuditEvent(
            agent_id=agent_id,
            request_id=request_id,
            action=action,
            resource=resource,
            decision=decision,
            reason=reason,
            prev_hash=prev_hash,
        )
        event.this_hash = event.compute_this_hash()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        return event

    def read_all(self) -> list[AuditEvent]:
        events: list[AuditEvent] = []
        if not self.path.exists():
            return events
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                events.append(AuditEvent.model_validate(raw))
            except (json.JSONDecodeError, ValueError):
                continue
        return events

    def verify_chain(self) -> bool:
        expected_prev = GENESIS_HASH
        for event in self.read_all():
            if event.prev_hash != expected_prev:
                return False
            if event.this_hash != event.compute_this_hash():
                return False
            expected_prev = event.this_hash
        return True

    def _last_hash(self) -> str:
        events = self.read_all()
        if not events:
            return GENESIS_HASH
        return events[-1].this_hash
