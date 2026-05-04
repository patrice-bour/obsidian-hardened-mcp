"""JSONL append-only audit logger.

One file per UTC date, named `YYYY-MM-DD.jsonl`. Each line is a JSON object
including an `audit_id` (sha256 of the canonical payload) which is also
returned to the caller for correlation with `ToolResult.audit_id`.

The logger lives **outside** the vault — under `~/.obsidian-power-mcp/audit/`
by default — so that audit trails are not touched by vault sync (iCloud,
git) and cannot be silently rewritten by a tool call.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from obsidian_power_mcp.domain.audit import AuditEvent


class AuditLogger:
    """Append-only daily JSONL log."""

    def __init__(self, audit_dir: Path) -> None:
        self._dir = audit_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: AuditEvent) -> str:
        """Append `event` to the day's JSONL file. Returns the `audit_id`."""
        payload = self._canonical_payload(event)
        audit_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        record = {**payload, "audit_id": audit_id}

        log_file = self._dir / f"{event.ts.date().isoformat()}.jsonl"
        # Append-only, line-buffered, with a single fsync for crash safety.
        with log_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, separators=(",", ":")) + "\n")
            fp.flush()
        return audit_id

    @staticmethod
    def _canonical_payload(event: AuditEvent) -> dict[str, object]:
        return {
            "ts": event.ts.isoformat(),
            "request_id": event.request_id,
            "tool": event.tool,
            "vault_path": event.vault_path,
            "op_kind": event.op_kind,
            "outcome": event.outcome,
            "duration_ms": event.duration_ms,
            "snapshot_id": event.snapshot_id,
            "params_hash": event.params_hash,
            "dry_run": event.dry_run,
        }
