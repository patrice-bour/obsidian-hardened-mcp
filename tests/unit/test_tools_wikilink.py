"""Tests for tools.wikilink — resolve_wikilink."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.tools.wikilink import resolve_wikilink


@pytest.fixture
def config(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault)


@pytest.fixture
def linked_vault(tmp_vault: Path) -> Path:
    (tmp_vault / "01_Notes" / "Alpha.md").write_text("Alpha content\n")
    (tmp_vault / "01_Notes" / "Beta.md").write_text("Beta content\n")
    sub_a = tmp_vault / "01_Notes" / "ProjectA"
    sub_a.mkdir()
    (sub_a / "Shared.md").write_text("ProjectA shared\n")
    sub_b = tmp_vault / "01_Notes" / "ProjectB"
    sub_b.mkdir()
    (sub_b / "Shared.md").write_text("ProjectB shared\n")
    return tmp_vault


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_empty_target_is_rejected(self, config: AppConfig) -> None:
        result = resolve_wikilink(config, "")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_whitespace_target_is_rejected(self, config: AppConfig) -> None:
        result = resolve_wikilink(config, "   ")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_brackets_in_target_are_stripped(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        # Caller-provided full `[[...]]` form is accepted as a courtesy.
        result = resolve_wikilink(config, "[[Alpha]]")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/Alpha.md"


# ---------------------------------------------------------------------------
# Basic resolution by basename
# ---------------------------------------------------------------------------


class TestBasenameResolution:
    def test_unique_basename_resolves(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Alpha")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/Alpha.md"
        assert result.data["ambiguous"] is False

    def test_unknown_target_resolves_to_null(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "NonExistent")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] is None
        assert result.data["ambiguous"] is False
        assert result.data["candidates"] == []

    def test_explicit_md_extension_handled(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Alpha.md")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/Alpha.md"


# ---------------------------------------------------------------------------
# Ambiguous resolution
# ---------------------------------------------------------------------------


class TestAmbiguousResolution:
    def test_two_notes_same_basename_are_ambiguous(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Shared")
        assert result.ok
        assert result.data is not None
        assert result.data["ambiguous"] is True
        assert result.data["resolved"] is None
        assert sorted(result.data["candidates"]) == [
            "01_Notes/ProjectA/Shared.md",
            "01_Notes/ProjectB/Shared.md",
        ]

    def test_from_path_prefers_same_folder(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        """Obsidian shortest-relative: when ambiguous, prefer the candidate
        in the same folder as `from_path` (and otherwise nearest)."""
        result = resolve_wikilink(
            config,
            "Shared",
            from_path="01_Notes/ProjectA/some_other.md",
        )
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/ProjectA/Shared.md"
        assert result.data["ambiguous"] is False


# ---------------------------------------------------------------------------
# Path-form resolution
# ---------------------------------------------------------------------------


class TestPathForm:
    def test_explicit_path_resolves_directly(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "01_Notes/ProjectB/Shared")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/ProjectB/Shared.md"
        assert result.data["ambiguous"] is False

    def test_explicit_path_with_extension(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "01_Notes/Alpha.md")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/Alpha.md"

    def test_explicit_path_traversal_rejected(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "../escape")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE


# ---------------------------------------------------------------------------
# Wikilink syntax: alias, heading, block id
# ---------------------------------------------------------------------------


class TestWikilinkSyntax:
    def test_alias_is_parsed(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Alpha|My Alpha")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/Alpha.md"
        assert result.data["alias"] == "My Alpha"

    def test_heading_is_parsed(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Alpha#Some Heading")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/Alpha.md"
        assert result.data["heading"] == "Some Heading"
        assert result.data["block_id"] is None

    def test_block_id_is_parsed(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Alpha#^abc123")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/Alpha.md"
        assert result.data["block_id"] == "abc123"
        assert result.data["heading"] is None

    def test_heading_and_alias_combined(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Alpha#Section|Display")
        assert result.ok
        assert result.data is not None
        assert result.data["heading"] == "Section"
        assert result.data["alias"] == "Display"

    def test_target_with_only_alias_no_link(self, config: AppConfig) -> None:
        # `[[|alias]]` — no target, treat as invalid.
        result = resolve_wikilink(config, "|alias")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH


# ---------------------------------------------------------------------------
# `target` field preserved verbatim in the result
# ---------------------------------------------------------------------------


class TestTargetEcho:
    def test_original_target_string_is_echoed(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, "Alpha#Section|Display")
        assert result.ok
        assert result.data is not None
        assert result.data["target"] == "Alpha#Section|Display"


# ---------------------------------------------------------------------------
# C2 — `from_path` disjoint from candidates → still ambiguous (M5 review fix)
# ---------------------------------------------------------------------------


class TestFromPathDisjoint:
    def test_from_path_outside_any_candidate_folder_stays_ambiguous(
        self, config: AppConfig, tmp_vault: Path
    ) -> None:
        # Two `Shared.md` candidates under 01_Notes/Project{A,B}/, plus a
        # `from_path` rooted under 02_Other/ — share no folder prefix
        # with either candidate. Result MUST surface as ambiguous.
        (tmp_vault / "01_Notes" / "ProjectA").mkdir(exist_ok=True)
        (tmp_vault / "01_Notes" / "ProjectB").mkdir(exist_ok=True)
        (tmp_vault / "01_Notes" / "ProjectA" / "Shared.md").write_text("a")
        (tmp_vault / "01_Notes" / "ProjectB" / "Shared.md").write_text("b")
        (tmp_vault / "02_Other").mkdir()
        (tmp_vault / "02_Other" / "elsewhere.md").write_text("e")

        result = resolve_wikilink(
            config, "Shared", from_path="02_Other/elsewhere.md"
        )
        assert result.ok
        assert result.data is not None
        assert result.data["ambiguous"] is True
        assert result.data["resolved"] is None
        assert sorted(result.data["candidates"]) == [
            "01_Notes/ProjectA/Shared.md",
            "01_Notes/ProjectB/Shared.md",
        ]


# ---------------------------------------------------------------------------
# M3 — Windows backslash in path-form is normalised (M5 review fix)
# ---------------------------------------------------------------------------


class TestBackslashPath:
    def test_backslash_path_form_resolves_like_forward_slash(
        self, config: AppConfig, linked_vault: Path
    ) -> None:
        result = resolve_wikilink(config, r"01_Notes\ProjectA\Shared")
        assert result.ok
        assert result.data is not None
        assert result.data["resolved"] == "01_Notes/ProjectA/Shared.md"


# ---------------------------------------------------------------------------
# M5 — mismatched [[]] brackets are rejected (M5 review fix)
# ---------------------------------------------------------------------------


class TestMismatchedBrackets:
    def test_only_open_brackets_rejected(self, config: AppConfig) -> None:
        result = resolve_wikilink(config, "[[Target")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_only_close_brackets_rejected(self, config: AppConfig) -> None:
        result = resolve_wikilink(config, "Target]]")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH
