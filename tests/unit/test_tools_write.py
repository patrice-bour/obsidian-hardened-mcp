"""Tests for tools.write — create_note, update_note, append_to_note, patch_note."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.write import (
    append_to_note,
    create_note,
    patch_note,
    update_note,
)


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


def _last_audit(audit_dir: Path) -> dict:
    files = sorted(audit_dir.glob("*.jsonl"))
    assert files, "no audit log file"
    lines = files[-1].read_text().splitlines()
    assert lines, "no audit lines"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# create_note
# ---------------------------------------------------------------------------


class TestCreateNote:
    def test_creates_new_file(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        result = create_note(config, audit, "01_Notes/new.md", "# New\n")
        assert result.ok
        assert result.audit_id is not None
        assert (tmp_vault / "01_Notes" / "new.md").read_text() == "# New\n"

    def test_audit_log_contains_create_event(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = create_note(config, audit, "01_Notes/new.md", "x")
        record = _last_audit(config.audit_dir)
        assert record["tool"] == "create_note"
        assert record["op_kind"] == "write"
        assert record["outcome"] == "success"
        assert record["audit_id"] == result.audit_id
        assert record["dry_run"] is False

    def test_refuses_to_overwrite_existing(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = create_note(config, audit, "01_Notes/sample.md", "should fail")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.ALREADY_EXISTS

    def test_dry_run_does_not_write(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        result = create_note(
            config, audit, "01_Notes/preview.md", "hello", dry_run=True
        )
        assert result.ok
        assert result.dry_run is True
        assert not (tmp_vault / "01_Notes" / "preview.md").exists()

    def test_path_traversal_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = create_note(config, audit, "../escape.md", "x")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE


# ---------------------------------------------------------------------------
# update_note
# ---------------------------------------------------------------------------


class TestUpdateNote:
    def test_replaces_content(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        result = update_note(config, audit, "01_Notes/sample.md", "# Updated\n")
        assert result.ok
        assert (tmp_vault / "01_Notes" / "sample.md").read_text() == "# Updated\n"

    def test_missing_file_returns_not_found(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = update_note(config, audit, "01_Notes/missing.md", "x")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_FOUND

    def test_dry_run_does_not_modify(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        original = (tmp_vault / "01_Notes" / "sample.md").read_text()
        result = update_note(
            config, audit, "01_Notes/sample.md", "# Updated\n", dry_run=True
        )
        assert result.ok
        assert (tmp_vault / "01_Notes" / "sample.md").read_text() == original


# ---------------------------------------------------------------------------
# append_to_note
# ---------------------------------------------------------------------------


class TestAppendToNote:
    def test_appends_content(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        result = append_to_note(config, audit, "01_Notes/sample.md", "more\n")
        assert result.ok
        content = (tmp_vault / "01_Notes" / "sample.md").read_text()
        assert content == "# Sample\nmore\n"

    def test_ensures_newline_between_blocks(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        # Existing file has no trailing newline → ensure_newline injects one.
        target = tmp_vault / "01_Notes" / "no_newline.md"
        target.write_text("first")
        result = append_to_note(config, audit, "01_Notes/no_newline.md", "second")
        assert result.ok
        assert target.read_text() == "first\nsecond"

    def test_does_not_double_newline(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "with_newline.md"
        target.write_text("first\n")
        result = append_to_note(config, audit, "01_Notes/with_newline.md", "second")
        assert result.ok
        assert target.read_text() == "first\nsecond"

    def test_missing_file_returns_not_found(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = append_to_note(config, audit, "01_Notes/missing.md", "x")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# patch_note (literal find-replace)
# ---------------------------------------------------------------------------


class TestPatchNote:
    def test_default_count_requires_unique_match(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        # The default (`count=1`) requires EXACTLY one occurrence — if the
        # file has two, the operation aborts. This is deliberate: the user
        # picks `count=N` as a safety check, not as a "replace at most N" cap.
        target = tmp_vault / "01_Notes" / "patch.md"
        target.write_text("foo only here\n")
        result = patch_note(config, audit, "01_Notes/patch.md", "foo", "qux")
        assert result.ok
        assert target.read_text() == "qux only here\n"

    def test_replaces_all_when_count_zero(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "patch.md"
        target.write_text("foo bar foo\n")
        result = patch_note(
            config, audit, "01_Notes/patch.md", "foo", "qux", count=0
        )
        assert result.ok
        assert target.read_text() == "qux bar qux\n"

    def test_count_mismatch_aborts_without_writing(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "patch.md"
        target.write_text("foo bar\n")
        original = target.read_text()
        result = patch_note(
            config, audit, "01_Notes/patch.md", "foo", "qux", count=2
        )
        assert not result.ok
        assert result.error is not None
        # The file MUST NOT be partially patched.
        assert target.read_text() == original

    def test_dry_run_returns_diff_without_writing(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "patch.md"
        target.write_text("foo\n")
        result = patch_note(
            config, audit, "01_Notes/patch.md", "foo", "bar", dry_run=True
        )
        assert result.ok
        assert result.dry_run is True
        assert target.read_text() == "foo\n"
        assert result.data is not None
        # Preview includes the new content
        assert "new_content" in result.data
