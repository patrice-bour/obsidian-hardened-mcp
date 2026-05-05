"""Tests for security.audit_logger — JSONL append-only audit trail.

`audit_id` is a CONTENT HASH:
    sha256(tool, vault_path, op_kind, outcome, params_hash, dry_run, snapshot_id)

It deliberately does NOT include `ts`, `request_id` or `duration_ms` — those
are volatile per-call values. Two events with the same content fingerprint
(same tool against same path with same params) yield the same `audit_id`,
which is what "deterministic for replay/correlation" means.

The PER-EVENT unique identifier is `request_id` (one per MCP tool call,
propagated through every `_emit` made within that call).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from obsidian_hardened_mcp.domain.audit import AuditEvent
from obsidian_hardened_mcp.security.audit_logger import AuditLogger


def _event(**overrides: object) -> AuditEvent:
    base: dict[str, object] = {
        "ts": datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC),
        "request_id": "req-x",
        "tool": "create_note",
        "vault_path": "01_Notes/new.md",
        "op_kind": "write",
        "outcome": "success",
        "duration_ms": 12,
        "params_hash": "deadbeef",
        "snapshot_id": None,
        "dry_run": False,
    }
    base.update(overrides)
    return AuditEvent(**base)  # type: ignore[arg-type]


class TestJsonlPersistence:
    def test_writes_jsonl_to_dated_file(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        audit_id = logger.log(_event(request_id="abc123"))

        # Audit ID is a 64-char hex string (sha256).
        assert len(audit_id) == 64
        assert all(c in "0123456789abcdef" for c in audit_id)

        log_file = tmp_path / "audit" / "2026-05-04.jsonl"
        assert log_file.exists()

        record = json.loads(log_file.read_text().strip())
        assert record["request_id"] == "abc123"
        assert record["tool"] == "create_note"
        assert record["vault_path"] == "01_Notes/new.md"
        assert record["audit_id"] == audit_id

    def test_appends_multiple_events(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        for i in range(3):
            logger.log(_event(request_id=f"req-{i}", vault_path=f"f-{i}.md"))

        lines = (tmp_path / "audit" / "2026-05-04.jsonl").read_text().splitlines()
        assert [json.loads(line)["request_id"] for line in lines] == [
            "req-0",
            "req-1",
            "req-2",
        ]

    def test_separate_files_per_day(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        logger.log(_event(ts=datetime(2026, 5, 4, 23, 59, 59, tzinfo=UTC)))
        logger.log(_event(ts=datetime(2026, 5, 5, 0, 0, 1, tzinfo=UTC)))
        assert (tmp_path / "audit" / "2026-05-04.jsonl").exists()
        assert (tmp_path / "audit" / "2026-05-05.jsonl").exists()

    def test_audit_dir_is_created_if_missing(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "audit"
        AuditLogger(audit_dir=deep)
        assert deep.exists() and deep.is_dir()

    def test_records_optional_fields(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        logger.log(
            _event(
                tool="delete_note",
                vault_path="x.md",
                op_kind="destructive",
                duration_ms=20,
                snapshot_id="snap-abc",
                params_hash="cafebabe",
                dry_run=False,
            )
        )
        record = json.loads(
            (tmp_path / "audit" / "2026-05-04.jsonl").read_text().strip()
        )
        assert record["snapshot_id"] == "snap-abc"
        assert record["params_hash"] == "cafebabe"
        assert record["dry_run"] is False


class TestContentHashSemantics:
    """`audit_id` is a content hash — it MUST ignore volatile fields."""

    def test_audit_id_is_independent_of_request_id(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        id1 = logger.log(_event(request_id="alpha"))
        id2 = logger.log(_event(request_id="beta"))
        assert id1 == id2

    def test_audit_id_is_independent_of_timestamp(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        id1 = logger.log(_event(ts=datetime(2026, 5, 4, 1, 0, 0, tzinfo=UTC)))
        id2 = logger.log(_event(ts=datetime(2026, 5, 4, 23, 0, 0, tzinfo=UTC)))
        assert id1 == id2

    def test_audit_id_is_independent_of_duration_ms(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        id1 = logger.log(_event(duration_ms=1))
        id2 = logger.log(_event(duration_ms=999))
        assert id1 == id2

    def test_audit_id_changes_when_tool_changes(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        assert logger.log(_event(tool="create_note")) != logger.log(
            _event(tool="update_note")
        )

    def test_audit_id_changes_when_vault_path_changes(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        assert logger.log(_event(vault_path="a.md")) != logger.log(
            _event(vault_path="b.md")
        )

    def test_audit_id_changes_when_outcome_changes(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        assert logger.log(_event(outcome="success")) != logger.log(
            _event(outcome="failure")
        )

    def test_audit_id_changes_when_params_hash_changes(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        assert logger.log(_event(params_hash="a")) != logger.log(
            _event(params_hash="b")
        )

    def test_audit_id_changes_when_dry_run_flag_changes(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        assert logger.log(_event(dry_run=True)) != logger.log(_event(dry_run=False))

    def test_audit_id_changes_when_snapshot_id_changes(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        assert logger.log(_event(snapshot_id="s1")) != logger.log(
            _event(snapshot_id="s2")
        )
