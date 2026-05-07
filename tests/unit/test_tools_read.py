"""Unit tests for tools.read."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.tools.read import list_notes, read_multiple_notes, read_note


@pytest.fixture
def config(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault)


class TestReadNote:
    def test_happy_path(self, config: AppConfig) -> None:
        result = read_note(config, "01_Notes/sample.md")
        assert result.ok
        assert result.data == {
            "path": "01_Notes/sample.md",
            "content": "# Sample\n",
            "size": 9,
        }

    def test_path_traversal_returns_path_escape(self, config: AppConfig) -> None:
        result = read_note(config, "../escape.md")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE

    def test_absolute_path_returns_absolute_path(self, config: AppConfig) -> None:
        result = read_note(config, "/etc/passwd")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.ABSOLUTE_PATH

    def test_forbidden_zone(self, config: AppConfig) -> None:
        result = read_note(config, ".obsidian/config.json")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.FORBIDDEN_ZONE

    def test_missing_file(self, config: AppConfig) -> None:
        result = read_note(config, "01_Notes/missing.md")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_FOUND

    def test_directory_returns_not_a_file(self, config: AppConfig) -> None:
        result = read_note(config, "01_Notes")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.NOT_A_FILE


class TestListNotes:
    def test_lists_all_markdown_files(self, config: AppConfig) -> None:
        result = list_notes(config)
        assert result.ok
        assert result.data is not None
        paths = result.data["notes"]
        assert "00_Journal/2026-05-04.md" in paths
        assert "01_Notes/sample.md" in paths
        assert "_VAULT.md" in paths
        # Forbidden zones are NEVER listed.
        assert all(not p.startswith(".obsidian") for p in paths)
        assert all(not p.startswith(".git") for p in paths)
        assert all(not p.startswith(".trash") for p in paths)

    def test_lists_only_markdown(self, config: AppConfig, tmp_vault: Path) -> None:
        (tmp_vault / "01_Notes" / "image.png").write_bytes(b"\x89PNG")
        (tmp_vault / "01_Notes" / "data.json").write_text("{}")
        result = list_notes(config)
        assert result.ok
        assert result.data is not None
        paths = result.data["notes"]
        assert all(p.endswith(".md") for p in paths)

    def test_filter_by_folder(self, config: AppConfig) -> None:
        result = list_notes(config, folder="00_Journal")
        assert result.ok
        assert result.data is not None
        paths = result.data["notes"]
        assert all(p.startswith("00_Journal/") for p in paths)

    def test_invalid_folder_returns_error(self, config: AppConfig) -> None:
        result = list_notes(config, folder="../escape")
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PATH_ESCAPE

    def test_pagination_limit(self, config: AppConfig, tmp_vault: Path) -> None:
        for i in range(5):
            (tmp_vault / "01_Notes" / f"note_{i}.md").write_text(f"# {i}\n")
        result = list_notes(config, limit=3)
        assert result.ok
        assert result.data is not None
        assert len(result.data["notes"]) == 3
        assert result.data["truncated"] is True

    def test_pagination_not_truncated(self, config: AppConfig) -> None:
        result = list_notes(config, limit=200)
        assert result.ok
        assert result.data is not None
        assert result.data["truncated"] is False

    def test_results_are_sorted(self, config: AppConfig, tmp_vault: Path) -> None:
        (tmp_vault / "01_Notes" / "zzz.md").write_text("z")
        (tmp_vault / "01_Notes" / "aaa.md").write_text("a")
        result = list_notes(config, folder="01_Notes")
        assert result.ok
        assert result.data is not None
        paths = result.data["notes"]
        assert paths == sorted(paths)

    def test_negative_limit_is_clamped_to_one(self, config: AppConfig) -> None:
        result = list_notes(config, limit=-1)
        assert result.ok
        assert result.data is not None
        assert result.data["limit"] == 1

    def test_excessive_limit_is_clamped_to_max_batch(
        self, config: AppConfig
    ) -> None:
        result = list_notes(config, limit=10_000)
        assert result.ok
        assert result.data is not None
        assert result.data["limit"] == config.max_batch


class TestReadMultipleNotes:
    def test_empty_paths_rejected(self, config: AppConfig) -> None:
        result = read_multiple_notes(config, [])
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH
        assert "empty" in result.error.message.lower()

    def test_too_many_paths_rejected(self, config: AppConfig) -> None:
        # max_batch defaults to 500; pass 501 paths.
        paths = [f"01_Notes/{i}.md" for i in range(config.max_batch + 1)]
        result = read_multiple_notes(config, paths)
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.BATCH_TOO_LARGE
        assert str(config.max_batch) in result.error.message

    def test_single_success(self, config: AppConfig) -> None:
        result = read_multiple_notes(config, ["01_Notes/sample.md"])
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert len(results) == 1
        assert results[0]["path"] == "01_Notes/sample.md"
        assert results[0]["content"] == "# Sample\n"
        assert results[0]["size"] == 9
        assert "error" not in results[0]
        assert result.data["cumulative_bytes"] == 9
        assert result.data["stopped_early"] is False

    def test_all_succeed_preserves_order(self, config: AppConfig) -> None:
        paths = ["01_Notes/sample.md", "_VAULT.md", "00_Journal/2026-05-04.md"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert [r["path"] for r in results] == paths
        assert all("content" in r for r in results)

    def test_partial_success_not_found(self, config: AppConfig) -> None:
        paths = ["01_Notes/sample.md", "01_Notes/missing.md", "_VAULT.md"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.NOT_FOUND.value
        assert "content" in results[2]

    def test_partial_success_path_escape(self, config: AppConfig) -> None:
        paths = ["01_Notes/sample.md", "../escape.md"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.PATH_ESCAPE.value
        assert results[1]["path"] == "../escape.md"

    def test_partial_success_forbidden_zone(self, config: AppConfig) -> None:
        paths = ["01_Notes/sample.md", ".obsidian/config.json"]
        result = read_multiple_notes(config, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.FORBIDDEN_ZONE.value

    def test_partial_success_file_too_large(
        self, tmp_vault: Path
    ) -> None:
        # Tight max_file_size_mb so a small file already breaks it.
        cfg = AppConfig(vault_root=tmp_vault, max_file_size_mb=1)
        big = tmp_vault / "01_Notes" / "big.md"
        big.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB > 1 MB cap

        result = read_multiple_notes(
            cfg, ["01_Notes/sample.md", "01_Notes/big.md"]
        )
        assert result.ok
        assert result.data is not None
        results = result.data["results"]
        assert "content" in results[0]
        assert results[1]["error"]["code"] == ErrorCode.FILE_TOO_LARGE.value

    def test_cumulative_cap_stops_iteration(self, tmp_vault: Path) -> None:
        # 3 files of 6 MB each; 10 MB cap; max_file_size_mb=8 (over the
        # individual cap to allow 6 MB files).
        cfg = AppConfig(
            vault_root=tmp_vault,
            max_file_size_mb=8,
            max_batch_bytes=10 * 1024 * 1024,
        )
        for name in ("a.md", "b.md", "c.md"):
            (tmp_vault / "01_Notes" / name).write_bytes(b"x" * (6 * 1024 * 1024))

        paths = ["01_Notes/a.md", "01_Notes/b.md", "01_Notes/c.md"]
        result = read_multiple_notes(cfg, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]

        assert "content" in results[0]
        assert "content" in results[1]
        assert results[2]["error"]["code"] == ErrorCode.BATCH_TOO_LARGE.value
        assert "after index 1" in results[2]["error"]["message"]
        assert result.data["stopped_early"] is True
        assert result.data["cumulative_bytes"] == 12 * 1024 * 1024

    def test_cumulative_cap_marks_remaining(self, tmp_vault: Path) -> None:
        cfg = AppConfig(
            vault_root=tmp_vault,
            max_file_size_mb=8,
            max_batch_bytes=10 * 1024 * 1024,
        )
        for name in ("a.md", "b.md", "c.md", "d.md", "e.md"):
            (tmp_vault / "01_Notes" / name).write_bytes(b"x" * (4 * 1024 * 1024))

        paths = [f"01_Notes/{n}" for n in ("a.md", "b.md", "c.md", "d.md", "e.md")]
        result = read_multiple_notes(cfg, paths)
        assert result.ok
        assert result.data is not None
        results = result.data["results"]

        # First three succeed (4+4+4 = 12 MB > 10 MB cap, stops after #3)
        assert "content" in results[0]
        assert "content" in results[1]
        assert "content" in results[2]
        assert results[3]["error"]["code"] == ErrorCode.BATCH_TOO_LARGE.value
        assert results[4]["error"]["code"] == ErrorCode.BATCH_TOO_LARGE.value
        assert result.data["stopped_early"] is True
        assert result.data["cumulative_bytes"] == 12 * 1024 * 1024

    def test_no_early_stop_when_under_cap(self, config: AppConfig) -> None:
        result = read_multiple_notes(
            config, ["01_Notes/sample.md", "_VAULT.md", "00_Journal/2026-05-04.md"]
        )
        assert result.ok
        assert result.data is not None
        assert result.data["stopped_early"] is False
