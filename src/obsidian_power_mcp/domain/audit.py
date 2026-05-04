"""Audit event model.

Every write or destructive operation emits an `AuditEvent` to the JSONL
audit log. The event captures:
    - When (UTC ISO-8601 timestamp)
    - What (tool name, op_kind)
    - Where (vault-relative path)
    - How it ended (outcome, duration_ms)
    - Optional snapshot reference, params hash, dry-run flag
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

OpKind = Literal["read", "write", "destructive", "meta"]
Outcome = Literal["success", "failure"]


class AuditEvent(BaseModel):
    """A single audit log entry.

    `audit_id` is filled in by the logger (sha256 of canonical JSON).
    """

    model_config = ConfigDict(frozen=True)

    ts: datetime
    request_id: str
    tool: str
    vault_path: str
    op_kind: OpKind
    outcome: Outcome
    duration_ms: int
    snapshot_id: str | None = None
    params_hash: str | None = None
    dry_run: bool = False
