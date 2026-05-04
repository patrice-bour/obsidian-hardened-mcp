"""Tests for tools.destructive.rename_note — 2-phase rename with backlinks.

Same protocol as `delete_note`: phase 1 issues a token + preview;
phase 2 consumes it, snapshots, and renames. `update_backlinks=True`
adds a best-effort scan that rewrites `[[OldBasename]]` /
`[[OldBasename.md]]` to the new basename across the vault.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.domain.results import ErrorCode
from obsidian_full_mcp.security.audit_logger import AuditLogger
from obsidian_full_mcp.security.confirm import ConfirmRegistry
from obsidian_full_mcp.tools.destructive import rename_note


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
# Phase 1 — preview, no mutation
# ---------------------------------------------------------------------------


class TestRenamePhase1:
    def test_phase1_returns_token_and_preview(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
        )
        assert result.ok
        assert result.dry_run is True
        assert result.data is not None
        assert "confirm_token" in result.data
        assert result.data["would_become"] == "01_Notes/renamed.md"
        # Source untouched, destination not created.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()
        assert not (tmp_vault / "01_Notes" / "renamed.md").exists()

    def test_phase1_appends_md_when_new_name_lacks_extension(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed",
        )
        assert result.ok
        assert result.data is not None
        assert result.data["would_become"] == "01_Notes/renamed.md"


# ---------------------------------------------------------------------------
# Validation: new_name must be a filename (no slash / no traversal)
# ---------------------------------------------------------------------------


class TestRenameValidation:
    def test_new_name_with_slash_is_invalid(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="subdir/renamed.md",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_new_name_with_traversal_is_invalid(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="..",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_empty_new_name_is_invalid(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_missing_source_returns_not_found(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/missing.md",
            new_name="renamed.md",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_FOUND

    def test_destination_exists_returns_already_exists(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        (tmp_vault / "01_Notes" / "exists.md").write_text("# Exists\n")
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="exists.md",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.ALREADY_EXISTS


# ---------------------------------------------------------------------------
# Phase 2 — consume + snapshot + rename
# ---------------------------------------------------------------------------


class TestRenamePhase2:
    def test_phase2_renames_file(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
        )
        assert first.data is not None
        token = first.data["confirm_token"]
        commit = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            confirm_token=token,
        )
        assert commit.ok
        assert commit.dry_run is False
        assert not (tmp_vault / "01_Notes" / "sample.md").exists()
        assert (tmp_vault / "01_Notes" / "renamed.md").exists()
        assert (tmp_vault / "01_Notes" / "renamed.md").read_text() == "# Sample\n"

    def test_phase2_creates_snapshot(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        first = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        commit = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            confirm_token=token,
        )
        assert commit.data is not None
        snap_id = commit.data["snapshot_id"]
        snap_copy = (
            tmp_vault / ".ofmcp-trash" / snap_id / "01_Notes" / "sample.md"
        )
        assert snap_copy.exists()
        assert snap_copy.read_text() == "# Sample\n"

    def test_phase2_with_swapped_new_name_returns_payload_mismatch(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        tmp_vault: Path,
    ) -> None:
        # Token is bound to (path, new_name). Caller cannot swap new_name.
        first = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="hijack.md",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PAYLOAD_MISMATCH
        # No file moved, no destination created.
        assert (tmp_vault / "01_Notes" / "sample.md").exists()
        assert not (tmp_vault / "01_Notes" / "hijack.md").exists()


# ---------------------------------------------------------------------------
# update_backlinks
# ---------------------------------------------------------------------------


class TestRenameBacklinks:
    @pytest.fixture
    def vault_with_backlinks(self, tmp_vault: Path) -> Path:
        # Three notes referencing 'sample' via various wikilink forms.
        (tmp_vault / "01_Notes" / "ref_bare.md").write_text(
            "see [[sample]] please\n"
        )
        (tmp_vault / "01_Notes" / "ref_with_md.md").write_text(
            "see [[sample.md]] please\n"
        )
        (tmp_vault / "01_Notes" / "ref_alias.md").write_text(
            "see [[sample|My Sample]] please\n"
        )
        (tmp_vault / "01_Notes" / "no_ref.md").write_text(
            "no reference at all\n"
        )
        # Free-text occurrence of 'sample' that is NOT a wikilink — must
        # not be touched.
        (tmp_vault / "01_Notes" / "freetext.md").write_text(
            "the word sample appears here without brackets\n"
        )
        return tmp_vault

    def test_phase1_enumerates_backlinks_without_writing(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        vault_with_backlinks: Path,
    ) -> None:
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            update_backlinks=True,
        )
        assert result.ok
        assert result.data is not None
        backlinks = result.data["backlinks_to_update"]
        # The three wikilink-referencing files (bare, .md, alias) — but NOT
        # the free-text file.
        assert isinstance(backlinks, list)
        rels = sorted(backlinks)
        assert "01_Notes/ref_bare.md" in rels
        assert "01_Notes/ref_with_md.md" in rels
        assert "01_Notes/ref_alias.md" in rels
        assert "01_Notes/freetext.md" not in rels
        assert "01_Notes/no_ref.md" not in rels
        # Files untouched in phase 1.
        assert (
            "[[sample]]"
            in (vault_with_backlinks / "01_Notes" / "ref_bare.md").read_text()
        )

    def test_phase2_rewrites_all_backlinks(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        vault_with_backlinks: Path,
    ) -> None:
        first = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            update_backlinks=True,
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        commit = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            confirm_token=token,
            update_backlinks=True,
        )
        assert commit.ok
        # Bare wikilink rewritten.
        bare = (vault_with_backlinks / "01_Notes" / "ref_bare.md").read_text()
        assert "[[renamed]]" in bare
        assert "[[sample]]" not in bare
        # `.md`-suffixed rewritten.
        with_md = (
            vault_with_backlinks / "01_Notes" / "ref_with_md.md"
        ).read_text()
        assert "[[renamed.md]]" in with_md
        # Alias preserved.
        alias = (vault_with_backlinks / "01_Notes" / "ref_alias.md").read_text()
        assert "[[renamed|My Sample]]" in alias
        # Free-text file untouched.
        freetext = (
            vault_with_backlinks / "01_Notes" / "freetext.md"
        ).read_text()
        assert "the word sample appears here" in freetext

    def test_phase2_emits_one_write_audit_per_rewritten_file(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        vault_with_backlinks: Path,
    ) -> None:
        first = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            update_backlinks=True,
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            confirm_token=token,
            update_backlinks=True,
        )
        records = _all_audits(config.audit_dir)
        # Filter to the commit audit's request_id (latest destructive event).
        destructive_records = [r for r in records if r["op_kind"] == "destructive"]
        commit_record = [
            r for r in destructive_records if r["dry_run"] is False
        ][-1]
        rid = commit_record["request_id"]
        write_records = [
            r
            for r in records
            if r["request_id"] == rid and r["op_kind"] == "write"
        ]
        assert len(write_records) == 3  # bare + with_md + alias
        # The destructive (rename) audit comes too.
        assert commit_record["snapshot_id"]
        # M6.5 — every backlink-rewrite audit must be attributed to the
        # CALLER tool, not hardcoded to "rename_note".
        for record in write_records:
            assert record["tool"] == "rename_note"

    def test_unreadable_file_is_skipped_not_crashed(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        vault_with_backlinks: Path,
    ) -> None:
        # Strip read permission from one referencing file. The scan must
        # log it as skipped, not raise.
        unreadable = vault_with_backlinks / "01_Notes" / "ref_bare.md"
        os.chmod(unreadable, 0o000)
        try:
            first = rename_note(
                config,
                audit,
                registry,
                path="01_Notes/sample.md",
                new_name="renamed.md",
                update_backlinks=True,
            )
            assert first.ok
            assert first.data is not None
            # Either the file was not enumerable (skipped from candidates)
            # or it was counted in skipped_unreadable. The phase-1 must
            # still succeed.
            assert "skipped_unreadable" in first.data
            assert isinstance(first.data["skipped_unreadable"], int)
        finally:
            os.chmod(unreadable, 0o644)

    def test_token_is_bound_to_update_backlinks_flag(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
        vault_with_backlinks: Path,
    ) -> None:
        # Issue with update_backlinks=True; phase 2 with =False -> mismatch.
        first = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            update_backlinks=True,
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        result = rename_note(
            config,
            audit,
            registry,
            path="01_Notes/sample.md",
            new_name="renamed.md",
            confirm_token=token,
            update_backlinks=False,  # <-- different
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PAYLOAD_MISMATCH
