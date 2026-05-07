"""Unit tests for tools.frontmatter (M2 — read-only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.frontmatter import get_frontmatter


class TestNormalizeTag:
    def test_strips_hash_prefix(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag("#wip") == "wip"

    def test_strips_whitespace(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag("  wip  ") == "wip"

    def test_strips_hash_then_whitespace(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag(" #wip ") == "wip"

    def test_accepts_hierarchy(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import _normalize_tag

        assert _normalize_tag("project/aaa") == "project/aaa"

    def test_rejects_empty_after_strip(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import (
            _InvalidTagError,
            _normalize_tag,
        )

        with pytest.raises(_InvalidTagError):
            _normalize_tag("#")
        with pytest.raises(_InvalidTagError):
            _normalize_tag("   ")

    def test_rejects_invalid_chars(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import (
            _InvalidTagError,
            _normalize_tag,
        )

        for bad in ("a b", "a\nb", "a\tb", "tag!", "tag?"):
            with pytest.raises(_InvalidTagError):
                _normalize_tag(bad)

    def test_rejects_leading_or_trailing_slash(self) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import (
            _InvalidTagError,
            _normalize_tag,
        )

        for bad in ("/wip", "wip/", "/wip/"):
            with pytest.raises(_InvalidTagError):
                _normalize_tag(bad)


class TestManageTags:
    @pytest.fixture
    def config(self, tmp_vault: Path) -> AppConfig:
        return AppConfig(vault_root=tmp_vault)

    @pytest.fixture
    def audit(self, tmp_path: Path) -> AuditLogger:
        return AuditLogger(tmp_path / "audit")

    def test_add_with_empty_tags_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "add", [])
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    def test_add_with_none_tags_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "add", None)
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    def test_remove_with_empty_tags_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "remove", [])
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    def test_invalid_tag_chars_rejected(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(
            config, audit, "01_Notes/sample.md", "add", ["a b"]
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_TAG

    def test_list_empty_when_no_tags_key(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(config, audit, "01_Notes/sample.md", "list")
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == []
        assert result.data["path"] == "01_Notes/sample.md"

    def test_list_returns_existing_tags(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - wip\n  - draft\n---\nbody\n"
        )
        result = manage_tags(config, audit, "01_Notes/tagged.md", "list")
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["wip", "draft"]

    def test_list_emits_no_audit(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        audit_dir = tmp_path / "audit"
        logger = AuditLogger(audit_dir)
        _ = manage_tags(config, logger, "01_Notes/sample.md", "list")
        assert (not audit_dir.exists()) or not list(audit_dir.glob("*.jsonl"))

    def test_list_rejects_non_list_tags(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "csv.md").write_text(
            "---\ntags: a, b, c\n---\nbody\n"
        )
        result = manage_tags(config, audit, "01_Notes/csv.md", "list")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.MALFORMED_FRONTMATTER

    def test_add_to_empty_creates_tags_key(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(
            config, audit, "01_Notes/sample.md", "add", ["wip"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["wip"]
        assert result.data["added"] == ["wip"]
        assert result.data["removed"] == []
        assert result.data["op"] == "add"

    def test_add_dedupe_silent(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "add", ["a", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a", "b"]
        assert result.data["added"] == ["b"]
        assert result.data["removed"] == []

    def test_add_preserves_existing_order_then_new(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - z\n  - a\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "add", ["m", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["z", "a", "m", "b"]

    def test_add_hash_prefix_stripped(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(
            config, audit, "01_Notes/sample.md", "add", ["#wip"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["wip"]

    def test_add_no_change_when_all_already_present(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        path = tmp_vault / "01_Notes" / "tagged.md"
        path.write_text("---\ntags:\n  - a\n  - b\n---\nbody\n")
        mtime_before = path.stat().st_mtime_ns
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "add", ["a", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a", "b"]
        assert result.data["added"] == []
        assert path.stat().st_mtime_ns == mtime_before

    def test_remove_existing_tag(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n  - b\n  - c\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "remove", ["b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a", "c"]
        assert result.data["removed"] == ["b"]
        assert result.data["added"] == []

    def test_remove_absent_tag_silent_noop(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        path = tmp_vault / "01_Notes" / "tagged.md"
        path.write_text("---\ntags:\n  - a\n---\nbody\n")
        mtime_before = path.stat().st_mtime_ns
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "remove", ["does-not-exist"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == ["a"]
        assert result.data["removed"] == []
        assert path.stat().st_mtime_ns == mtime_before

    def test_remove_all_drops_tags_key(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        from obsidian_hardened_mcp.frontmatter import parse_note
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        (tmp_vault / "01_Notes" / "tagged.md").write_text(
            "---\ntags:\n  - a\n  - b\n---\nbody\n"
        )
        result = manage_tags(
            config, audit, "01_Notes/tagged.md", "remove", ["a", "b"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == []
        assert result.data["removed"] == ["a", "b"]

        text = (tmp_vault / "01_Notes" / "tagged.md").read_text()
        parsed = parse_note(text)
        assert parsed.frontmatter is None or "tags" not in parsed.frontmatter

    def test_remove_with_no_tags_key_noop(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        from obsidian_hardened_mcp.tools.frontmatter import manage_tags

        result = manage_tags(
            config, audit, "01_Notes/sample.md", "remove", ["wip"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["tags"] == []
        assert result.data["removed"] == []


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
