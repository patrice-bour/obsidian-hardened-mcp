"""Tests for the atomic frontmatter field operations (M3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode
from obsidian_power_mcp.frontmatter import parse_note
from obsidian_power_mcp.security.audit_logger import AuditLogger
from obsidian_power_mcp.tools.frontmatter import (
    delete_frontmatter_field,
    merge_frontmatter,
    set_frontmatter_field,
)


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


@pytest.fixture
def fm_note(tmp_vault: Path) -> Path:
    target = tmp_vault / "01_Notes" / "fm.md"
    target.write_text(
        "---\n"
        "# header comment\n"
        "title: Hello\n"
        "tags:\n"
        "  - foo\n"
        "  - bar\n"
        "---\n"
        "Body\n"
    )
    return target


# ---------------------------------------------------------------------------
# set_frontmatter_field
# ---------------------------------------------------------------------------


class TestSetFrontmatterField:
    def test_adds_new_field(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "author", "Patrice"
        )
        assert result.ok
        new = parse_note(fm_note.read_text())
        assert new.frontmatter is not None
        assert new.frontmatter["author"] == "Patrice"

    def test_overwrites_existing_field(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "title", "Updated"
        )
        assert result.ok
        new = parse_note(fm_note.read_text())
        assert new.frontmatter is not None
        assert new.frontmatter["title"] == "Updated"

    def test_preserves_comments_and_other_fields(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "title", "Updated"
        )
        rendered = fm_note.read_text()
        assert "# header comment" in rendered
        assert "tags:" in rendered

    def test_creates_frontmatter_if_absent(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "no_fm.md"
        target.write_text("# Just markdown\n")
        result = set_frontmatter_field(
            config, audit, "01_Notes/no_fm.md", "title", "Added"
        )
        assert result.ok
        new = parse_note(target.read_text())
        assert new.frontmatter is not None
        assert new.frontmatter["title"] == "Added"
        assert new.body == "# Just markdown\n"

    def test_dry_run_does_not_write(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        original = fm_note.read_text()
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "title", "no", dry_run=True
        )
        assert result.ok
        assert result.dry_run is True
        assert fm_note.read_text() == original

    def test_complex_value_accepted(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        # list value
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "tags", ["a", "b", "c"]
        )
        assert result.ok
        new = parse_note(fm_note.read_text())
        assert new.frontmatter is not None
        assert list(new.frontmatter["tags"]) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# delete_frontmatter_field
# ---------------------------------------------------------------------------


class TestDeleteFrontmatterField:
    def test_removes_existing_field(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = delete_frontmatter_field(
            config, audit, "01_Notes/fm.md", "title"
        )
        assert result.ok
        new = parse_note(fm_note.read_text())
        assert new.frontmatter is not None
        assert "title" not in new.frontmatter
        assert "tags" in new.frontmatter

    def test_missing_field_returns_field_not_found(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = delete_frontmatter_field(
            config, audit, "01_Notes/fm.md", "missing"
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.FIELD_NOT_FOUND

    def test_no_frontmatter_returns_field_not_found(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "no_fm.md"
        target.write_text("# Plain\n")
        result = delete_frontmatter_field(
            config, audit, "01_Notes/no_fm.md", "title"
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.FIELD_NOT_FOUND


# ---------------------------------------------------------------------------
# merge_frontmatter
# ---------------------------------------------------------------------------


class TestMergeFrontmatter:
    def test_shallow_merge_overrides_top_level(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        patch = {"title": "New", "author": "Patrice"}
        result = merge_frontmatter(
            config, audit, "01_Notes/fm.md", patch, mode="shallow"
        )
        assert result.ok
        new = parse_note(fm_note.read_text())
        assert new.frontmatter is not None
        assert new.frontmatter["title"] == "New"
        assert new.frontmatter["author"] == "Patrice"
        assert "tags" in new.frontmatter

    def test_shallow_merge_replaces_nested_structures(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "nested.md"
        target.write_text(
            "---\n"
            "meta:\n"
            "  a: 1\n"
            "  b: 2\n"
            "---\n"
        )
        result = merge_frontmatter(
            config, audit, "01_Notes/nested.md", {"meta": {"c": 3}}, mode="shallow"
        )
        assert result.ok
        new = parse_note(target.read_text())
        assert new.frontmatter is not None
        # Shallow → entire `meta` replaced
        assert dict(new.frontmatter["meta"]) == {"c": 3}

    def test_deep_merge_recurses_into_mappings(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "nested.md"
        target.write_text(
            "---\n"
            "meta:\n"
            "  a: 1\n"
            "  b: 2\n"
            "---\n"
        )
        result = merge_frontmatter(
            config, audit, "01_Notes/nested.md", {"meta": {"c": 3}}, mode="deep"
        )
        assert result.ok
        new = parse_note(target.read_text())
        assert new.frontmatter is not None
        # Deep → existing `a`,`b` retained, `c` added
        assert dict(new.frontmatter["meta"]) == {"a": 1, "b": 2, "c": 3}

    def test_merge_creates_frontmatter_when_absent(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "no_fm.md"
        target.write_text("body\n")
        result = merge_frontmatter(
            config, audit, "01_Notes/no_fm.md", {"title": "New"}, mode="shallow"
        )
        assert result.ok
        new = parse_note(target.read_text())
        assert new.frontmatter == {"title": "New"}
