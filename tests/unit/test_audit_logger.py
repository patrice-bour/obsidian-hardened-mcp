"""Tests for security.audit_logger — JSONL append-only audit trail."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from obsidian_power_mcp.domain.audit import AuditEvent
from obsidian_power_mcp.security.audit_logger import AuditLogger


class TestAuditLogger:
    def test_writes_jsonl_to_dated_file(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        event = AuditEvent(
            ts=datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC),
            request_id="abc123",
            tool="create_note",
            vault_path="01_Notes/new.md",
            op_kind="write",
            outcome="success",
            duration_ms=12,
        )
        audit_id = logger.log(event)

        # Audit ID is a 64-char hex string (sha256).
        assert len(audit_id) == 64
        assert all(c in "0123456789abcdef" for c in audit_id)

        # File exists at audit_dir/<date>.jsonl
        log_file = tmp_path / "audit" / "2026-05-04.jsonl"
        assert log_file.exists()

        # Content is valid JSON, one line.
        line = log_file.read_text().strip()
        record = json.loads(line)
        assert record["request_id"] == "abc123"
        assert record["tool"] == "create_note"
        assert record["vault_path"] == "01_Notes/new.md"
        assert record["op_kind"] == "write"
        assert record["outcome"] == "success"
        assert record["audit_id"] == audit_id

    def test_appends_multiple_events(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        ts = datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC)
        for i in range(3):
            logger.log(
                AuditEvent(
                    ts=ts,
                    request_id=f"req-{i}",
                    tool="update_note",
                    vault_path=f"f-{i}.md",
                    op_kind="write",
                    outcome="success",
                    duration_ms=i,
                )
            )

        log_file = tmp_path / "audit" / "2026-05-04.jsonl"
        lines = log_file.read_text().splitlines()
        assert len(lines) == 3
        assert [json.loads(line)["request_id"] for line in lines] == [
            "req-0",
            "req-1",
            "req-2",
        ]

    def test_separate_files_per_day(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        d1 = datetime(2026, 5, 4, 23, 59, 59, tzinfo=UTC)
        d2 = datetime(2026, 5, 5, 0, 0, 1, tzinfo=UTC)
        for ts in (d1, d2):
            logger.log(
                AuditEvent(
                    ts=ts,
                    request_id="x",
                    tool="t",
                    vault_path="p",
                    op_kind="write",
                    outcome="success",
                    duration_ms=0,
                )
            )
        assert (tmp_path / "audit" / "2026-05-04.jsonl").exists()
        assert (tmp_path / "audit" / "2026-05-05.jsonl").exists()

    def test_audit_id_is_deterministic_for_same_payload(
        self, tmp_path: Path
    ) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        ts = datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC)
        kwargs = dict(
            ts=ts,
            request_id="req-x",
            tool="create_note",
            vault_path="a.md",
            op_kind="write",
            outcome="success",
            duration_ms=5,
        )
        id1 = logger.log(AuditEvent(**kwargs))  # type: ignore[arg-type]
        id2 = logger.log(AuditEvent(**kwargs))  # type: ignore[arg-type]
        assert id1 == id2

    def test_audit_id_changes_when_payload_changes(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        ts = datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC)
        id1 = logger.log(
            AuditEvent(
                ts=ts,
                request_id="r",
                tool="t",
                vault_path="a.md",
                op_kind="write",
                outcome="success",
                duration_ms=0,
            )
        )
        id2 = logger.log(
            AuditEvent(
                ts=ts,
                request_id="r",
                tool="t",
                vault_path="b.md",  # different path
                op_kind="write",
                outcome="success",
                duration_ms=0,
            )
        )
        assert id1 != id2

    def test_records_optional_fields(self, tmp_path: Path) -> None:
        logger = AuditLogger(audit_dir=tmp_path / "audit")
        event = AuditEvent(
            ts=datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC),
            request_id="r",
            tool="delete_note",
            vault_path="x.md",
            op_kind="destructive",
            outcome="success",
            duration_ms=20,
            snapshot_id="snap-abc",
            params_hash="cafebabe",
            dry_run=False,
        )
        logger.log(event)
        log_file = tmp_path / "audit" / "2026-05-04.jsonl"
        record = json.loads(log_file.read_text().strip())
        assert record["snapshot_id"] == "snap-abc"
        assert record["params_hash"] == "cafebabe"
        assert record["dry_run"] is False

    def test_audit_dir_is_created_if_missing(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "audit"
        AuditLogger(audit_dir=deep)
        assert deep.exists() and deep.is_dir()
