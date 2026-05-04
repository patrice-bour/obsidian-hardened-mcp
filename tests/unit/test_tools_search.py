"""Tests for tools.search — search_notes."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode
from obsidian_power_mcp.tools.search import search_notes


@pytest.fixture
def config(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault)


@pytest.fixture
def populated_vault(tmp_vault: Path) -> Path:
    (tmp_vault / "01_Notes" / "alpha.md").write_text(
        "---\ntype: note\ntags: [foo, bar]\ntitle: Alpha\n---\n"
        "Lorem ipsum dolor sit amet. The needle hides here.\n"
    )
    (tmp_vault / "01_Notes" / "beta.md").write_text(
        "---\ntype: project\ntags: [baz]\ntitle: Beta\n---\n"
        "Something else entirely. Not the keyword.\n"
    )
    (tmp_vault / "01_Notes" / "gamma.md").write_text(
        "---\ntype: note\ntags: [foo]\n---\n"
        "Multiple lines\nWith the needle in line two\nAnd more after.\n"
    )
    sub = tmp_vault / "01_Notes" / "sub"
    sub.mkdir()
    (sub / "delta.md").write_text("---\ntype: note\n---\nFoobar needle deep\n")
    return tmp_vault


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_empty_query_is_rejected(self, config: AppConfig) -> None:
        result = search_notes(config, "")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_unknown_mode_is_rejected(self, config: AppConfig) -> None:
        result = search_notes(config, "foo", mode="invalid")  # type: ignore[arg-type]
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_invalid_folder_is_rejected(self, config: AppConfig) -> None:
        result = search_notes(config, "foo", folder="../escape")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE


# ---------------------------------------------------------------------------
# Fulltext mode
# ---------------------------------------------------------------------------


class TestFulltextMode:
    def test_finds_match_in_body(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "needle", mode="fulltext")
        assert result.ok
        assert result.data is not None
        paths = [m["path"] for m in result.data["matches"]]
        assert "01_Notes/alpha.md" in paths
        assert "01_Notes/gamma.md" in paths
        assert "01_Notes/sub/delta.md" in paths
        assert "01_Notes/beta.md" not in paths

    def test_case_insensitive_by_default(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "NEEDLE", mode="fulltext")
        assert result.ok
        assert result.data is not None
        assert len(result.data["matches"]) >= 3

    def test_returns_snippet_for_each_match(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "needle", mode="fulltext")
        assert result.ok
        assert result.data is not None
        for m in result.data["matches"]:
            assert "snippet" in m
            assert "needle" in m["snippet"].lower()

    def test_no_matches_returns_empty_list_not_error(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "this-string-does-not-exist", mode="fulltext")
        assert result.ok
        assert result.data is not None
        assert result.data["matches"] == []


# ---------------------------------------------------------------------------
# Frontmatter mode
# ---------------------------------------------------------------------------


class TestFrontmatterMode:
    def test_matches_frontmatter_value(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "Alpha", mode="frontmatter")
        assert result.ok
        assert result.data is not None
        paths = [m["path"] for m in result.data["matches"]]
        assert "01_Notes/alpha.md" in paths
        # Body containing "needle" should NOT show up in frontmatter mode.
        assert all(m["match_kind"] != "fulltext" for m in result.data["matches"])

    def test_matches_tag_value(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "baz", mode="frontmatter")
        assert result.ok
        assert result.data is not None
        paths = [m["path"] for m in result.data["matches"]]
        assert "01_Notes/beta.md" in paths


# ---------------------------------------------------------------------------
# Combined mode (default)
# ---------------------------------------------------------------------------


class TestCombinedMode:
    def test_returns_both_fulltext_and_frontmatter_hits(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "Alpha", mode="combined")
        assert result.ok
        assert result.data is not None
        # alpha.md matches frontmatter (title); no body has "Alpha" literal
        # but combined mode covers both.
        assert any(
            m["path"] == "01_Notes/alpha.md" for m in result.data["matches"]
        )


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_folder_restricts_search(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "needle", folder="01_Notes/sub")
        assert result.ok
        assert result.data is not None
        paths = [m["path"] for m in result.data["matches"]]
        assert paths == ["01_Notes/sub/delta.md"]

    def test_tag_filter_restricts_to_tagged_notes(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "needle", tag="foo")
        assert result.ok
        assert result.data is not None
        paths = {m["path"] for m in result.data["matches"]}
        # Only alpha (tags: foo, bar) and gamma (tags: foo) match the tag.
        assert paths == {"01_Notes/alpha.md", "01_Notes/gamma.md"}

    def test_type_filter(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "Beta", type_filter="project")
        assert result.ok
        assert result.data is not None
        paths = {m["path"] for m in result.data["matches"]}
        assert paths == {"01_Notes/beta.md"}


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def test_limit_truncates_results(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        for i in range(20):
            (tmp_vault / "01_Notes" / f"note_{i:02d}.md").write_text(
                f"---\ntitle: x\n---\nKEYWORD line {i}\n"
            )
        result = search_notes(config, "KEYWORD", limit=5)
        assert result.ok
        assert result.data is not None
        assert len(result.data["matches"]) == 5
        assert result.data["truncated"] is True

    def test_no_truncation_when_under_limit(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "needle", limit=100)
        assert result.ok
        assert result.data is not None
        assert result.data["truncated"] is False


# ---------------------------------------------------------------------------
# Forbidden zones
# ---------------------------------------------------------------------------


class TestForbiddenZones:
    def test_forbidden_dirs_are_not_searched(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        # Plant a markdown file inside `.obsidian/` (which is supposed to
        # be forbidden) — search MUST NOT see it.
        (tmp_vault / ".obsidian" / "secret.md").write_text("KEYWORD here\n")
        result = search_notes(config, "KEYWORD")
        assert result.ok
        assert result.data is not None
        paths = [m["path"] for m in result.data["matches"]]
        assert all(not p.startswith(".obsidian") for p in paths)


# ---------------------------------------------------------------------------
# C1 — combined mode reports both kinds when both hit (M5 review fix)
# ---------------------------------------------------------------------------


class TestCombinedReportsBoth:
    def test_both_fulltext_and_frontmatter_match_yield_combined_kind(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        (tmp_vault / "01_Notes" / "double.md").write_text(
            "---\ntitle: Needle in title\n---\n"
            "Body line that also mentions needle.\n"
        )
        result = search_notes(config, "needle", mode="combined")
        assert result.ok
        assert result.data is not None
        match = next(
            m for m in result.data["matches"] if m["path"] == "01_Notes/double.md"
        )
        # The headline contract: both signals reach the client.
        assert match["match_kind"] == "combined"
        assert "needle" in match["snippet"].lower()
        assert match["frontmatter_field"] == "title"
        assert "needle" in match["frontmatter_snippet"].lower()


# ---------------------------------------------------------------------------
# C3 — search exposes skipped_read / skipped_parse counts (M5 review fix)
# ---------------------------------------------------------------------------


class TestSkippedCounts:
    def test_zero_skips_when_all_files_clean(
        self, config: AppConfig, populated_vault: Path
    ) -> None:
        result = search_notes(config, "needle")
        assert result.ok
        assert result.data is not None
        assert result.data["skipped_read"] == 0
        assert result.data["skipped_parse"] == 0

    def test_malformed_frontmatter_is_counted(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        # Top-level YAML sequence (not a mapping) → MalformedFrontmatterError.
        (tmp_vault / "01_Notes" / "bad.md").write_text(
            "---\n- one\n- two\n---\nbody KEYWORD\n"
        )
        (tmp_vault / "01_Notes" / "good.md").write_text("KEYWORD body\n")
        result = search_notes(config, "KEYWORD")
        assert result.ok
        assert result.data is not None
        assert result.data["skipped_parse"] >= 1
        # The clean file still surfaces despite the bad sibling.
        assert any(m["path"] == "01_Notes/good.md" for m in result.data["matches"])
