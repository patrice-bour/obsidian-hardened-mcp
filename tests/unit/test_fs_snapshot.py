"""Tests for fs.snapshot — pre-destruction file snapshots.

`snapshot_for_destruction` copies the targeted file into
`<snapshot_root>/<UTC-ts>-<short-hash>/<original-relative-path>` BEFORE
any destructive op runs. The destination lives under the vault's
`.ohmcp-trash/` (a forbidden zone for read tools), so snapshots are
preserved but never re-exposed via the MCP surface.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from obsidian_hardened_mcp.domain.vault_path import VaultPath
from obsidian_hardened_mcp.fs.snapshot import (
    SnapshotError,
    snapshot_for_destruction,
)

_SNAPSHOT_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def _vp(tmp_vault: Path, rel: str) -> VaultPath:
    return VaultPath.from_user(rel, tmp_vault)


def _trash(tmp_vault: Path) -> Path:
    return tmp_vault / ".ohmcp-trash"


class TestSnapshotFile:
    def test_copies_file_to_snapshot_root(self, tmp_vault: Path) -> None:
        vp = _vp(tmp_vault, "01_Notes/sample.md")
        snapshot_id = snapshot_for_destruction(
            vp, snapshot_root=_trash(tmp_vault)
        )
        # ID format: UTC ISO basic timestamp + dash + 8 hex chars
        assert _SNAPSHOT_ID_RE.match(snapshot_id), snapshot_id
        # Snapshot copy exists at the expected path
        copy = (
            _trash(tmp_vault)
            / snapshot_id
            / "01_Notes"
            / "sample.md"
        )
        assert copy.exists()
        assert copy.read_text() == "# Sample\n"

    def test_original_file_is_left_in_place(self, tmp_vault: Path) -> None:
        vp = _vp(tmp_vault, "01_Notes/sample.md")
        snapshot_for_destruction(vp, snapshot_root=_trash(tmp_vault))
        # Original unchanged.
        assert vp.absolute.exists()
        assert vp.absolute.read_text() == "# Sample\n"

    def test_preserves_metadata_via_copy2(self, tmp_vault: Path) -> None:
        vp = _vp(tmp_vault, "01_Notes/sample.md")
        # Set a known mtime on the source.
        target_time = 1700000000.0
        os.utime(vp.absolute, (target_time, target_time))
        snapshot_id = snapshot_for_destruction(
            vp, snapshot_root=_trash(tmp_vault)
        )
        copy = (
            _trash(tmp_vault) / snapshot_id / "01_Notes" / "sample.md"
        )
        # copy2 preserves mtime within filesystem resolution (1s).
        assert abs(copy.stat().st_mtime - target_time) < 2

    def test_creates_snapshot_root_if_missing(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        # Use a different snapshot_root that doesn't exist yet.
        custom_root = tmp_path / "custom" / "snapshots"
        vp = _vp(tmp_vault, "01_Notes/sample.md")
        snapshot_id = snapshot_for_destruction(vp, snapshot_root=custom_root)
        assert custom_root.exists()
        assert (custom_root / snapshot_id).is_dir()


class TestSnapshotIdUniqueness:
    def test_rapid_calls_yield_different_ids(self, tmp_vault: Path) -> None:
        # Stress test: 100 successive calls (M8 hardening per M6-10).
        # All complete within the same second on any modern machine, so
        # the only thing keeping ids unique is the 4-byte hex suffix
        # (1-in-4-billion collision per pair). 100 calls = ~5000 pairs;
        # collision probability ~1.2e-6 — safely below the test's
        # detection threshold under normal entropy.
        vp = _vp(tmp_vault, "01_Notes/sample.md")
        ids: set[str] = set()
        for _ in range(100):
            ids.add(
                snapshot_for_destruction(vp, snapshot_root=_trash(tmp_vault))
            )
        assert len(ids) == 100

    def test_calls_separated_by_a_second_still_differ(
        self, tmp_vault: Path
    ) -> None:
        vp = _vp(tmp_vault, "01_Notes/sample.md")
        a = snapshot_for_destruction(vp, snapshot_root=_trash(tmp_vault))
        time.sleep(1.05)
        b = snapshot_for_destruction(vp, snapshot_root=_trash(tmp_vault))
        assert a != b


class TestSnapshotDestinationIsTrash:
    def test_destination_under_ohmcp_trash(self, tmp_vault: Path) -> None:
        # The caller passes <vault>/.ohmcp-trash; the resulting snapshot
        # path MUST stay inside that forbidden zone (no traversal possible
        # since we use the file's vault-relative path verbatim).
        vp = _vp(tmp_vault, "01_Notes/sample.md")
        snapshot_id = snapshot_for_destruction(
            vp, snapshot_root=_trash(tmp_vault)
        )
        copy = (
            _trash(tmp_vault) / snapshot_id / "01_Notes" / "sample.md"
        )
        assert copy.is_file()
        # Resolved copy path lives strictly under the vault's forbidden zone.
        assert copy.resolve().is_relative_to(_trash(tmp_vault).resolve())


class TestSnapshotErrors:
    def test_missing_source_raises(self, tmp_vault: Path) -> None:
        # Build a VaultPath for a non-existent file (allowed — VaultPath
        # itself doesn't require the file to exist).
        vp = _vp(tmp_vault, "01_Notes/never-existed.md")
        with pytest.raises(SnapshotError):
            snapshot_for_destruction(vp, snapshot_root=_trash(tmp_vault))

    def test_directory_source_raises(self, tmp_vault: Path) -> None:
        # M6 only snapshots single files; directories are out of scope.
        (tmp_vault / "01_Notes" / "subdir").mkdir(exist_ok=True)
        vp = _vp(tmp_vault, "01_Notes/subdir")
        with pytest.raises(SnapshotError):
            snapshot_for_destruction(vp, snapshot_root=_trash(tmp_vault))
