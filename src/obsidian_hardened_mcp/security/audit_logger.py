# SPDX-License-Identifier: Apache-2.0
"""JSONL append-only audit logger.

One file per UTC date, named `YYYY-MM-DD.jsonl`. Each line is a JSON object
that includes an `audit_id` plus `request_id`:

- **`audit_id`** is a CONTENT HASH:
  ``sha256(tool, vault_path, op_kind, outcome, params_hash, dry_run, snapshot_id)``
  It deliberately ignores volatile fields (`ts`, `request_id`, `duration_ms`)
  so two events with the same content fingerprint share the same id —
  useful for replay/correlation/dedup.

- **`request_id`** is the per-call unique identifier, propagated from the
  tool boundary through every `_emit` made within a single tool call. It
  answers "what events came from THAT MCP call?".

The logger lives **outside** the vault — under `~/.obsidian-hardened-mcp/audit/`
by default — so audit trails are not touched by vault sync (iCloud, git)
and cannot be silently rewritten by a tool call.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from obsidian_hardened_mcp.domain.audit import AuditEvent

_CONTENT_HASH_KEYS = (
    "tool",
    "vault_path",
    "op_kind",
    "outcome",
    "params_hash",
    "dry_run",
    "snapshot_id",
)


class AuditLogger:
    """Append-only daily JSONL log."""

    def __init__(self, audit_dir: Path) -> None:
        self._dir = audit_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: AuditEvent) -> str:
        """Append `event` to the day's JSONL file. Returns the `audit_id`."""
        record = self._record_payload(event)
        audit_id = self._content_hash(record)
        record["audit_id"] = audit_id

        log_file = self._dir / f"{event.ts.date().isoformat()}.jsonl"
        with log_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, separators=(",", ":")) + "\n")
            fp.flush()
        return audit_id

    @staticmethod
    def _record_payload(event: AuditEvent) -> dict[str, object]:
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

    @staticmethod
    def _content_hash(record: dict[str, object]) -> str:
        """SHA256 over a stable subset of the record — ignores volatile fields.

        Canonical JSON (sorted keys, no whitespace) ensures the hash is
        reproducible across Python versions and dict insertion orders.
        """
        canonical = {key: record[key] for key in _CONTENT_HASH_KEYS}
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
