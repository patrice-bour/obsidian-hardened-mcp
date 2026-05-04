"""Tests for tools.destructive.move_note — 2-phase move across folders.

Mirrors `rename_note` but the destination is a *folder*, not a filename.
Cross-volume moves are out of scope for v0.1 (the vault is assumed to
live on a single filesystem).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode
from obsidian_power_mcp.security.audit_logger import AuditLogger
from obsidian_power_mcp.security.confirm import ConfirmRegistry
from obsidian_power_mcp.tools.destructive import move_note


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


@pytest.fixture
def registry() -> ConfirmRegistry:
    return ConfirmRegistry(secret=b"k" * 32)


def _all_audits(audit_dir: Path) -> list[dict]:
    files = sorted(audit_dir.glob("*.jsonl"))
    out: list[dict] = []
    for f in files:
        for line in f.read_text().splitlines():
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Phase 1 — preview
# ---------------------------------------------------------------------------


class TestMovePhase1:
    def test_phase1_returns_token_and_preview(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        result = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
        )
        assert result.ok
        assert result.dry_run is True
        assert result.data is not None
        assert "confirm_token" in result.data
        assert result.data["would_become"] == "00_Journal/sample.md"
        # File untouched.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()
        assert not (tmp_vault / "00_Journal" / "sample.md").exists()

    def test_phase1_creates_destination_folder_in_phase2(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        # New folder doesn't pre-exist.
        first = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="04_Archive",
        )
        assert first.ok
        token = first.data["confirm_token"]  # type: ignore[index]
        commit = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="04_Archive",
            confirm_token=token,
        )
        assert commit.ok
        assert (tmp_vault / "04_Archive" / "sample.md").exists()


# ---------------------------------------------------------------------------
# new_folder validation
# ---------------------------------------------------------------------------


class TestMoveValidation:
    def test_traversal_in_new_folder_is_path_escape(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="../escape",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE

    def test_absolute_new_folder_rejected(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="/etc",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.ABSOLUTE_PATH

    def test_forbidden_zone_destination_rejected(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder=".obsidian",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.FORBIDDEN_ZONE

    def test_destination_exists_returns_already_exists(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        # 00_Journal/sample.md exists already.
        (tmp_vault / "00_Journal" / "sample.md").write_text("existing\n")
        result = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.ALREADY_EXISTS

    def test_missing_source_returns_not_found(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = move_note(
            config,
            audit,
            registry,
            path="01_Notes/missing.md",
            new_folder="00_Journal",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# Phase 2 — snapshot + move
# ---------------------------------------------------------------------------


class TestMovePhase2:
    def test_phase2_moves_file(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        commit = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
            confirm_token=token,
        )
        assert commit.ok
        assert not (tmp_vault / "01_Notes" / "sample.md").exists()
        assert (tmp_vault / "00_Journal" / "sample.md").exists()
        assert (tmp_vault / "00_Journal" / "sample.md").read_text() == "# Sample\n"

    def test_phase2_creates_snapshot(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        commit = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
            confirm_token=token,
        )
        snap_id = commit.data["snapshot_id"]  # type: ignore[index]
        assert (
            tmp_vault
            / ".opmcp-trash"
            / snap_id
            / "01_Notes"
            / "sample.md"
        ).exists()

    def test_phase2_with_swapped_folder_returns_payload_mismatch(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        result = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="04_Hijack",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PAYLOAD_MISMATCH


# ---------------------------------------------------------------------------
# update_backlinks (basename unchanged but the PATH changes)
# ---------------------------------------------------------------------------


class TestMoveBacklinks:
    def test_backlinks_with_basename_collision_left_alone(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        # Move keeps the basename, so `[[sample]]` references are STILL
        # valid because Obsidian resolves by basename. We still go through
        # the backlink-rewrite path on caller request, but no rewrites
        # are needed because old_bare == new_bare.
        (tmp_vault / "01_Notes" / "ref.md").write_text("see [[sample]]\n")
        first = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
            update_backlinks=True,
        )
        assert first.ok
        token = first.data["confirm_token"]  # type: ignore[index]
        commit = move_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_folder="00_Journal",
            confirm_token=token,
            update_backlinks=True,
        )
        assert commit.ok
        # Reference unchanged (basename still matches).
        assert (
            "[[sample]]"
            in (tmp_vault / "01_Notes" / "ref.md").read_text()
        )
        # Backlinks_rewritten stays at 0.
        assert commit.data is not None
        assert commit.data.get("backlinks_rewritten", 0) == 0
