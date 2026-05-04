"""Unit tests for tools.frontmatter (M2 — read-only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode
from obsidian_power_mcp.tools.frontmatter import get_frontmatter


@pytest.fixture
def config(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault)


class TestGetFrontmatter:
    def test_returns_frontmatter_dict_and_body(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        (tmp_vault / "01_Notes" / "with_fm.md").write_text(
            "---\ntitle: Hello\ntags:\n  - foo\n---\nBody\n"
        )
        result = get_frontmatter(config, "01_Notes/with_fm.md")
        assert result.ok
        assert result.data is not None
        assert result.data["frontmatter"] == {"title": "Hello", "tags": ["foo"]}
        assert result.data["body_preview"] == "Body\n"
        assert result.data["has_frontmatter"] is True

    def test_no_frontmatter_returns_null(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        (tmp_vault / "01_Notes" / "plain.md").write_text("# Just markdown\n")
        result = get_frontmatter(config, "01_Notes/plain.md")
        assert result.ok
        assert result.data is not None
        assert result.data["frontmatter"] is None
        assert result.data["has_frontmatter"] is False

    def test_dates_are_serialised_as_iso_strings(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        (tmp_vault / "01_Notes" / "dated.md").write_text(
            "---\ndate: 2026-05-04\n---\n"
        )
        result = get_frontmatter(config, "01_Notes/dated.md")
        assert result.ok
        assert result.data is not None
        # Dates must be JSON-serialisable strings on the wire.
        assert result.data["frontmatter"] == {"date": "2026-05-04"}

    def test_path_traversal_rejected(self, config: AppConfig) -> None:
        result = get_frontmatter(config, "../escape.md")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE

    def test_missing_file(self, config: AppConfig) -> None:
        result = get_frontmatter(config, "01_Notes/missing.md")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_FOUND

    def test_unsafe_tag_rejected(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        (tmp_vault / "01_Notes" / "evil.md").write_text(
            "---\ndanger: !!python/object/apply:os.system ['id']\n---\n"
        )
        result = get_frontmatter(config, "01_Notes/evil.md")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSAFE_YAML
