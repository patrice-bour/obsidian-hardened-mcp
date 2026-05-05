"""Tests for fs.writer — atomic writes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_hardened_mcp.domain.vault_path import VaultPath
from obsidian_hardened_mcp.fs.writer import (
    AlreadyExistsError,
    atomic_write_text,
)


def _vp(rel: str, vault_root: Path) -> VaultPath:
    return VaultPath.from_user(rel, vault_root)


class TestAtomicWriteText:
    def test_creates_new_file(self, tmp_vault: Path) -> None:
        atomic_write_text(_vp("01_Notes/new.md", tmp_vault), "# New\n")
        assert (tmp_vault / "01_Notes" / "new.md").read_text() == "# New\n"

    def test_overwrites_existing_file(self, tmp_vault: Path) -> None:
        target = tmp_vault / "01_Notes" / "sample.md"
        original = target.read_text()
        atomic_write_text(_vp("01_Notes/sample.md", tmp_vault), "# Updated\n")
        assert target.read_text() == "# Updated\n"
        assert target.read_text() != original

    def test_creates_intermediate_directories(self, tmp_vault: Path) -> None:
        atomic_write_text(
            _vp("01_Notes/sub/deep/note.md", tmp_vault), "# Deep\n"
        )
        assert (tmp_vault / "01_Notes" / "sub" / "deep" / "note.md").exists()

    def test_writes_utf8(self, tmp_vault: Path) -> None:
        atomic_write_text(
            _vp("01_Notes/accents.md", tmp_vault), "Café à Paris ñ\n"
        )
        assert (tmp_vault / "01_Notes" / "accents.md").read_text(
            encoding="utf-8"
        ) == "Café à Paris ñ\n"

    def test_no_clobber_when_exclusive(self, tmp_vault: Path) -> None:
        with pytest.raises(AlreadyExistsError):
            atomic_write_text(
                _vp("01_Notes/sample.md", tmp_vault),
                "should not write",
                exclusive=True,
            )

    def test_temp_file_cleaned_on_write_error(self, tmp_vault: Path) -> None:
        """If the write fails mid-stream, the temp file MUST NOT linger."""
        target_dir = tmp_vault / "01_Notes"

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated disk full")

        with patch("os.fsync", side_effect=boom), pytest.raises(OSError):
            atomic_write_text(
                _vp("01_Notes/torn.md", tmp_vault), "boom"
            )

        # No leftover .tmp files in the target directory.
        leftovers = [p.name for p in target_dir.iterdir() if ".tmp." in p.name]
        assert leftovers == [], f"leftover tmp files: {leftovers}"

    def test_atomicity_no_partial_file_on_crash(self, tmp_vault: Path) -> None:
        """Writes are atomic: the target is either the old content or the new,
        never partial. Simulated by failing inside the rename."""
        target = tmp_vault / "01_Notes" / "sample.md"
        original = target.read_text()

        def fail_replace(*args: object, **kwargs: object) -> None:
            raise OSError("simulated")

        with patch("os.replace", side_effect=fail_replace), pytest.raises(OSError):
            atomic_write_text(
                _vp("01_Notes/sample.md", tmp_vault), "should not appear"
            )

        # The file was NOT replaced; original content is intact.
        assert target.read_text() == original
