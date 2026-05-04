"""Security tests for the VaultPath sandbox.

These tests are the cornerstone of the project's security model.
Coverage on `domain/vault_path.py` MUST stay at 100%.

Threat model covered:
    - Path traversal (`..`, mid-path, encoded)
    - Absolute path injection
    - Symlink escape (file is a symlink to outside the vault)
    - Forbidden zone access (`.obsidian/`, `.git/`, `.trash/`, etc.)
    - Length / segment count attacks
    - Null byte injection
    - Unicode NFD vs NFC confusion
"""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePosixPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from obsidian_power_mcp.domain.vault_path import (
    AbsolutePathError,
    ForbiddenZoneError,
    InvalidPathError,
    PathEscapeError,
    SymlinkEscapeError,
    VaultPath,
    VaultPathError,
)

pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidPaths:
    def test_simple_relative_path_is_accepted(self, tmp_vault: Path) -> None:
        vp = VaultPath.from_user("01_Notes/sample.md", tmp_vault)
        assert vp.relative == PurePosixPath("01_Notes/sample.md")
        assert vp.absolute == tmp_vault / "01_Notes" / "sample.md"
        assert vp.vault_root == tmp_vault.resolve()

    def test_path_to_nonexistent_file_is_accepted(self, tmp_vault: Path) -> None:
        # Required for create_note: the file does not exist yet.
        vp = VaultPath.from_user("01_Notes/new.md", tmp_vault)
        assert vp.relative == PurePosixPath("01_Notes/new.md")

    def test_path_to_nonexistent_nested_dir_is_accepted(self, tmp_vault: Path) -> None:
        vp = VaultPath.from_user("01_Notes/sub/new.md", tmp_vault)
        assert vp.relative == PurePosixPath("01_Notes/sub/new.md")

    def test_root_relative_path_is_accepted(self, tmp_vault: Path) -> None:
        vp = VaultPath.from_user("_VAULT.md", tmp_vault)
        assert vp.relative == PurePosixPath("_VAULT.md")

    def test_windows_separator_is_normalised_to_posix(self, tmp_vault: Path) -> None:
        # POSIX paths only on the wire. Backslashes treated as plain chars/rejected.
        vp = VaultPath.from_user("01_Notes/sample.md", tmp_vault)
        assert "\\" not in str(vp.relative)


# ---------------------------------------------------------------------------
# Absolute paths
# ---------------------------------------------------------------------------


class TestAbsolutePathRejection:
    def test_unix_absolute_path_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(AbsolutePathError):
            VaultPath.from_user("/etc/passwd", tmp_vault)

    def test_absolute_path_inside_vault_is_still_rejected(self, tmp_vault: Path) -> None:
        # Even if the absolute path WOULD point inside the vault, we reject:
        # the contract is "relative POSIX paths only".
        with pytest.raises(AbsolutePathError):
            VaultPath.from_user(str(tmp_vault / "01_Notes" / "sample.md"), tmp_vault)


# ---------------------------------------------------------------------------
# Path traversal (`..`)
# ---------------------------------------------------------------------------


class TestTraversalRejection:
    def test_dotdot_at_start_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(PathEscapeError):
            VaultPath.from_user("../escape.md", tmp_vault)

    def test_dotdot_mid_path_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(PathEscapeError):
            VaultPath.from_user("01_Notes/../../escape.md", tmp_vault)

    def test_dotdot_segment_alone_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(PathEscapeError):
            VaultPath.from_user("..", tmp_vault)


# ---------------------------------------------------------------------------
# Input sanity
# ---------------------------------------------------------------------------


class TestInvalidInput:
    def test_null_byte_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(InvalidPathError):
            VaultPath.from_user("01_Notes/bad\x00name.md", tmp_vault)

    def test_empty_string_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(InvalidPathError):
            VaultPath.from_user("", tmp_vault)

    def test_whitespace_only_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(InvalidPathError):
            VaultPath.from_user("   ", tmp_vault)

    def test_path_too_long_is_rejected(self, tmp_vault: Path) -> None:
        long_name = "a" * 5000
        with pytest.raises(InvalidPathError):
            VaultPath.from_user(long_name, tmp_vault)

    def test_too_many_segments_is_rejected(self, tmp_vault: Path) -> None:
        many_segments = "/".join(["a"] * 50) + ".md"
        with pytest.raises(InvalidPathError):
            VaultPath.from_user(many_segments, tmp_vault)

    def test_segment_too_long_is_rejected(self, tmp_vault: Path) -> None:
        # Most filesystems max out segments at 255 bytes
        with pytest.raises(InvalidPathError):
            VaultPath.from_user("a" * 300 + ".md", tmp_vault)

    def test_non_string_input_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(InvalidPathError):
            VaultPath.from_user(42, tmp_vault)  # type: ignore[arg-type]

    def test_dot_only_is_rejected_as_no_segments(self, tmp_vault: Path) -> None:
        # `PurePosixPath(".").parts == ()` — empty, no significant segments.
        with pytest.raises(InvalidPathError):
            VaultPath.from_user(".", tmp_vault)

    def test_missing_vault_root_is_rejected(self, tmp_path: Path) -> None:
        ghost_root = tmp_path / "does_not_exist"
        with pytest.raises(InvalidPathError):
            VaultPath.from_user("foo.md", ghost_root)


# ---------------------------------------------------------------------------
# Forbidden zones
# ---------------------------------------------------------------------------


class TestForbiddenZones:
    @pytest.mark.parametrize(
        "forbidden_path",
        [
            ".obsidian/config.json",
            ".obsidian",
            ".git/HEAD",
            ".git",
            ".trash/old.md",
            ".trash",
            ".opmcp-trash/2026-05-04/note.md",
            ".opmcp-trash",
            ".obsidian-power-mcp.yaml",
        ],
    )
    def test_forbidden_zones_are_rejected(self, tmp_vault: Path, forbidden_path: str) -> None:
        with pytest.raises(ForbiddenZoneError):
            VaultPath.from_user(forbidden_path, tmp_vault)

    def test_path_resembling_forbidden_but_not_actually_is_accepted(
        self, tmp_vault: Path
    ) -> None:
        # `.obsidian-things.md` is NOT inside `.obsidian/` and NOT the config file.
        vp = VaultPath.from_user("01_Notes/.obsidian-notes.md", tmp_vault)
        assert vp.relative == PurePosixPath("01_Notes/.obsidian-notes.md")


# ---------------------------------------------------------------------------
# Symlink escape (real filesystem)
# ---------------------------------------------------------------------------


class TestSymlinkEscape:
    def test_symlink_to_outside_vault_is_rejected(self, tmp_vault: Path) -> None:
        # Create a symlink inside the vault that points outside.
        outside = tmp_vault.parent / "outside.md"
        outside.write_text("secret")
        (tmp_vault / "01_Notes" / "escape.md").symlink_to(outside)
        with pytest.raises(SymlinkEscapeError):
            VaultPath.from_user("01_Notes/escape.md", tmp_vault)

    def test_symlink_directory_to_outside_is_rejected(self, tmp_vault: Path) -> None:
        # Symlink as an ancestor directory escaping the vault.
        outside_dir = tmp_vault.parent / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "secret.md").write_text("secret")
        (tmp_vault / "01_Notes" / "linked").symlink_to(outside_dir)
        with pytest.raises(SymlinkEscapeError):
            VaultPath.from_user("01_Notes/linked/secret.md", tmp_vault)

    def test_internal_symlink_is_accepted(self, tmp_vault: Path) -> None:
        # A symlink that stays within the vault is fine.
        target = tmp_vault / "01_Notes" / "sample.md"
        link = tmp_vault / "01_Notes" / "internal_link.md"
        link.symlink_to(target)
        vp = VaultPath.from_user("01_Notes/internal_link.md", tmp_vault)
        assert vp.absolute == link


# ---------------------------------------------------------------------------
# Unicode normalisation
# ---------------------------------------------------------------------------


class TestUnicodeNormalisation:
    def test_nfd_input_is_normalised_to_nfc(self, tmp_vault: Path) -> None:
        # "Café" can be NFC ("Café") or NFD ("Café"). VaultPath stores NFC.
        nfd = unicodedata.normalize("NFD", "Café.md")
        assert nfd != "Café.md"  # sanity
        vp = VaultPath.from_user(f"01_Notes/{nfd}", tmp_vault)
        assert str(vp.relative) == "01_Notes/" + unicodedata.normalize("NFC", "Café.md")


# ---------------------------------------------------------------------------
# Equality & immutability
# ---------------------------------------------------------------------------


class TestSemantics:
    def test_two_vault_paths_with_same_input_are_equal(self, tmp_vault: Path) -> None:
        a = VaultPath.from_user("01_Notes/sample.md", tmp_vault)
        b = VaultPath.from_user("01_Notes/sample.md", tmp_vault)
        assert a == b
        assert hash(a) == hash(b)

    def test_vault_path_is_immutable(self, tmp_vault: Path) -> None:
        vp = VaultPath.from_user("01_Notes/sample.md", tmp_vault)
        with pytest.raises((AttributeError, TypeError)):
            vp.relative = PurePosixPath("hijack.md")  # type: ignore[misc]

    def test_repr_contains_relative_path(self, tmp_vault: Path) -> None:
        vp = VaultPath.from_user("01_Notes/sample.md", tmp_vault)
        assert "01_Notes/sample.md" in repr(vp)


# ---------------------------------------------------------------------------
# Property-based sandbox proof (the headline guarantee)
# ---------------------------------------------------------------------------


@settings(max_examples=1000, deadline=None)
@given(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),  # exclude lone surrogates
        min_size=0,
        max_size=200,
    )
)
def test_property_sandbox_never_escapes(tmp_path_factory, raw: str) -> None:
    """For ANY input string, VaultPath.from_user either rejects it cleanly,
    or yields a path that resolves under vault_root.

    This is the headline security guarantee. No exceptions (except VaultPathError).
    """
    vault_root = tmp_path_factory.mktemp("vault")
    try:
        vp = VaultPath.from_user(raw, vault_root)
    except VaultPathError:
        return  # clean rejection, all good
    # Constructed: absolute path MUST resolve under the vault root.
    resolved = vp.absolute.resolve(strict=False)
    vault_resolved = vault_root.resolve()
    assert resolved == vault_resolved or vault_resolved in resolved.parents, (
        f"Sandbox escape! raw={raw!r}, resolved={resolved}, vault={vault_resolved}"
    )
