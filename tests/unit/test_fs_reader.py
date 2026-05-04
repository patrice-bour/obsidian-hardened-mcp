"""Unit tests for fs.reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_full_mcp.domain.vault_path import VaultPath
from obsidian_full_mcp.fs.reader import (
    FileOffloadedError,
    FileTooLargeError,
    NotAFileError,
    NotFoundError,
    read_text,
)


def _vp(rel: str, vault_root: Path) -> VaultPath:
    return VaultPath.from_user(rel, vault_root)


class TestReadText:
    def test_reads_existing_file(self, tmp_vault: Path) -> None:
        content = read_text(_vp("01_Notes/sample.md", tmp_vault))
        assert content == "# Sample\n"

    def test_missing_file_raises_not_found(self, tmp_vault: Path) -> None:
        with pytest.raises(NotFoundError):
            read_text(_vp("01_Notes/missing.md", tmp_vault))

    def test_directory_raises_not_a_file(self, tmp_vault: Path) -> None:
        with pytest.raises(NotAFileError):
            read_text(_vp("01_Notes", tmp_vault))

    def test_oversized_file_raises_too_large(self, tmp_vault: Path) -> None:
        big = tmp_vault / "01_Notes" / "huge.md"
        big.write_bytes(b"x" * (11 * 1024 * 1024))  # 11 MiB > 10 MiB default
        with pytest.raises(FileTooLargeError):
            read_text(_vp("01_Notes/huge.md", tmp_vault), max_size_bytes=10 * 1024 * 1024)

    def test_icloud_placeholder_raises_offloaded(self, tmp_vault: Path) -> None:
        # iCloud Drive replaces non-resident files with a metadata stub:
        # `<basename>.icloud` siblings to the real path. We treat any sibling
        # `.icloud` as the offloaded sentinel for that name.
        (tmp_vault / "01_Notes" / ".sample-offloaded.md.icloud").write_bytes(b"")
        with pytest.raises(FileOffloadedError):
            read_text(_vp("01_Notes/sample-offloaded.md", tmp_vault))

    def test_reads_utf8_with_accents(self, tmp_vault: Path) -> None:
        (tmp_vault / "01_Notes" / "café.md").write_text("Bonjour à toi", encoding="utf-8")
        assert read_text(_vp("01_Notes/café.md", tmp_vault)) == "Bonjour à toi"
