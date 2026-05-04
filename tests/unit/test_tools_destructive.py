"""Tests for tools.destructive — delete_note, rename_note, move_note.

Each tool implements a 2-phase confirm:
    phase 1 (confirm_token=None) -> preview + token, NO disk mutation;
    phase 2 (confirm_token=<from phase 1>) -> snapshot + mutation + audit.

`dry_run=True` is a third orthogonal mode: preview only, no token, no
mutation. The brief calls it "what would happen", as opposed to phase 1
which is "prepare an op".
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.domain.results import ErrorCode
from obsidian_full_mcp.security.audit_logger import AuditLogger
from obsidian_full_mcp.security.confirm import ConfirmRegistry
from obsidian_full_mcp.tools.destructive import delete_note

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


@pytest.fixture
def registry() -> ConfirmRegistry:
    return ConfirmRegistry(secret=b"k" * 32)


@pytest.fixture
def clocked_registry() -> Iterator[tuple[ConfirmRegistry, dict]]:
    """A registry with an injectable clock for expiry tests."""
    state = {"now": datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)}
    reg = ConfirmRegistry(
        secret=b"k" * 32, ttl_seconds=90, clock=lambda: state["now"]
    )
    yield reg, state


def _last_audit(audit_dir: Path) -> dict:
    files = sorted(audit_dir.glob("*.jsonl"))
    assert files, "no audit log file"
    lines = files[-1].read_text().splitlines()
    assert lines, "no audit lines"
    return json.loads(lines[-1])


def _all_audits(audit_dir: Path) -> list[dict]:
    files = sorted(audit_dir.glob("*.jsonl"))
    out: list[dict] = []
    for f in files:
        for line in f.read_text().splitlines():
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# delete_note — phase 1
# ---------------------------------------------------------------------------


class TestDeleteNotePhase1:
    def test_phase1_returns_token_and_preview(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        result = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert result.ok
        assert result.dry_run is True
        assert result.data is not None
        assert "confirm_token" in result.data
        assert isinstance(result.data["confirm_token"], str)
        assert len(result.data["confirm_token"]) == 86
        assert "expires_at" in result.data
        assert result.data["would_remove"] == "01_Notes/sample.md"
        assert "size_bytes" in result.data
        # File is untouched.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()

    def test_phase1_emits_destructive_audit_marked_dry_run(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        delete_note(config, audit, registry, path="01_Notes/sample.md")
        record = _last_audit(config.audit_dir)
        assert record["tool"] == "delete_note"
        assert record["op_kind"] == "destructive"
        assert record["outcome"] == "success"
        assert record["dry_run"] is True

    def test_phase1_on_path_traversal_returns_path_escape(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = delete_note(
            config, audit, registry, path="../escape.md"
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE

    def test_phase1_on_missing_file_returns_not_found(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = delete_note(
            config, audit, registry, path="01_Notes/missing.md"
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_FOUND

    def test_phase1_on_forbidden_zone_blocked(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        # `.obsidian/` is in the forbidden-zone list — VaultPath rejects it.
        result = delete_note(
            config, audit, registry, path=".obsidian/config.json"
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.FORBIDDEN_ZONE


# ---------------------------------------------------------------------------
# delete_note — phase 2 (token consumed)
# ---------------------------------------------------------------------------


class TestDeleteNotePhase2:
    def test_phase2_with_valid_token_deletes_file(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert first.ok and first.data is not None
        token = first.data["confirm_token"]

        second = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
        )
        assert second.ok
        assert second.dry_run is False
        assert second.audit_id is not None
        # File is gone.
        assert not (tmp_vault / "01_Notes" / "sample.md").exists()

    def test_phase2_creates_snapshot_in_ofmcp_trash(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert first.data is not None
        token = first.data["confirm_token"]

        second = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
        )
        assert second.ok and second.data is not None
        snapshot_id = second.data["snapshot_id"]
        assert isinstance(snapshot_id, str) and snapshot_id
        snap_copy = (
            tmp_vault / ".ofmcp-trash" / snapshot_id / "01_Notes" / "sample.md"
        )
        assert snap_copy.exists()
        assert snap_copy.read_text() == "# Sample\n"

    def test_phase2_emits_destructive_audit_with_snapshot_id(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        first = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert first.data is not None
        token = first.data["confirm_token"]
        second = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
        )
        assert second.data is not None
        snapshot_id = second.data["snapshot_id"]
        record = _last_audit(config.audit_dir)
        assert record["tool"] == "delete_note"
        assert record["op_kind"] == "destructive"
        assert record["outcome"] == "success"
        assert record["dry_run"] is False
        assert record["snapshot_id"] == snapshot_id

    def test_phase2_with_unknown_token_returns_invalid(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        result = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token="A" * 86,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_CONFIRMATION_TOKEN
        # File untouched.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()

    def test_phase2_with_expired_token_returns_expired(
        self,
        config: AppConfig,
        audit: AuditLogger,
        clocked_registry: tuple[ConfirmRegistry, dict],
        tmp_vault: Path,
    ) -> None:
        registry, state = clocked_registry
        first = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert first.data is not None
        token = first.data["confirm_token"]
        # Fast-forward past TTL.
        state["now"] = state["now"] + timedelta(seconds=91)
        result = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.EXPIRED_CONFIRMATION_TOKEN
        # File still there.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()

    def test_phase2_with_swapped_path_returns_payload_mismatch(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        # Issue a token for sample.md, but try to use it to delete journal.md.
        first = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert first.data is not None
        token = first.data["confirm_token"]

        result = delete_note(
            config,
            audit,
            registry,
            path="00_Journal/2026-05-04.md",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PAYLOAD_MISMATCH
        # Both files untouched.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()
        assert (tmp_vault / "00_Journal" / "2026-05-04.md").exists()

    def test_phase2_replay_after_consume_rejected(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert first.data is not None
        token = first.data["confirm_token"]
        # First consume succeeds.
        ok = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
        )
        assert ok.ok
        # Second attempt with the same token: file already gone, but the
        # token MUST be rejected before we even check existence — we want
        # INVALID_CONFIRMATION_TOKEN, not NOT_FOUND, so the audit trail
        # records the actual security-relevant outcome.
        replay = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
        )
        assert not replay.ok
        assert replay.error is not None
        assert replay.error.code is ErrorCode.INVALID_CONFIRMATION_TOKEN


# ---------------------------------------------------------------------------
# delete_note — dry_run=True (third orthogonal mode)
# ---------------------------------------------------------------------------


class TestDeleteNoteDryRun:
    def test_dry_run_returns_preview_without_token(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        result = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            dry_run=True,
        )
        assert result.ok
        assert result.dry_run is True
        assert result.data is not None
        # No token in dry-run mode.
        assert "confirm_token" not in result.data
        assert result.data["would_remove"] == "01_Notes/sample.md"
        # File untouched.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()

    def test_dry_run_emits_destructive_dry_run_audit(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            dry_run=True,
        )
        record = _last_audit(config.audit_dir)
        assert record["tool"] == "delete_note"
        assert record["op_kind"] == "destructive"
        assert record["outcome"] == "success"
        assert record["dry_run"] is True

    def test_dry_run_with_token_ignores_token(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        # If both a token AND dry_run=True are passed, dry_run wins —
        # the token is NOT consumed. (Caller can still issue and re-use.)
        first = delete_note(
            config, audit, registry, path="01_Notes/sample.md"
        )
        assert first.data is not None
        token = first.data["confirm_token"]
        result = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
            dry_run=True,
        )
        assert result.ok
        assert result.dry_run is True
        # The token still works for a real phase-2 call afterwards.
        commit = delete_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            confirm_token=token,
        )
        assert commit.ok
        assert not (tmp_vault / "01_Notes" / "sample.md").exists()


# ---------------------------------------------------------------------------
# request_id correlation
# ---------------------------------------------------------------------------


class TestRequestIdCorrelation:
    def test_each_call_emits_a_distinct_request_id(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        delete_note(config, audit, registry, path="01_Notes/sample.md")
        delete_note(
            config, audit, registry, path="00_Journal/2026-05-04.md"
        )
        records = _all_audits(config.audit_dir)
        ids = {r["request_id"] for r in records}
        assert len(ids) == len(records)
