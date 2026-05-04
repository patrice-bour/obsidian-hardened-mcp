"""Vault path sandbox.

Cornerstone of the security model. ALL filesystem-bound user inputs in tools
MUST flow through `VaultPath.from_user`. No exceptions, no shortcuts.

Threat model covered:
    - Absolute path injection (`/etc/passwd`)
    - Path traversal (`..`, mid-path, encoded)
    - Symlink escape (component is a symlink that resolves outside the vault)
    - Forbidden zone access (`.obsidian/`, `.git/`, `.trash/`, `.ofmcp-trash/`,
      and the project config file `.obsidian-full-mcp.yaml`)
    - Length / segment count attacks
    - Null byte injection
    - Unicode NFD vs NFC confusion (HFS+/APFS, iCloud)

Coverage on this module MUST stay at 100%.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_MAX_PATH_LENGTH = 4096
_MAX_SEGMENTS = 32
_MAX_SEGMENT_BYTES = 255

_FORBIDDEN_DIR_PREFIXES: frozenset[str] = frozenset(
    {".obsidian", ".git", ".trash", ".ofmcp-trash"}
)
_FORBIDDEN_FILES: frozenset[str] = frozenset({".obsidian-full-mcp.yaml"})


class VaultPathError(Exception):
    """Base for all VaultPath validation failures."""


class AbsolutePathError(VaultPathError):
    """The provided path is absolute; only relative POSIX paths are accepted."""


class PathEscapeError(VaultPathError):
    """The path contains `..` or otherwise resolves outside the vault root."""


class SymlinkEscapeError(VaultPathError):
    """A component of the path is a symlink whose target lies outside the vault."""


class ForbiddenZoneError(VaultPathError):
    """The path targets a reserved zone (`.obsidian/`, `.git/`, …)."""


class InvalidPathError(VaultPathError):
    """Generic input invalidity (empty, null byte, oversize, etc.)."""


@dataclass(frozen=True, slots=True)
class VaultPath:
    """Immutable, sandbox-validated path inside a vault.

    Attributes:
        vault_root: Absolute, fully-resolved (symlinks expanded) vault root.
        relative: POSIX-style, NFC-normalised, traversal-free relative path.

    Construct via `VaultPath.from_user`. The dataclass constructor is internal —
    do NOT bypass `from_user` from tool code.
    """

    vault_root: Path
    relative: PurePosixPath

    @classmethod
    def from_user(cls, raw: str, vault_root: Path) -> VaultPath:
        """Validate user-supplied input and return a `VaultPath`.

        Raises:
            InvalidPathError: empty, null byte, oversize, weird encoding
            AbsolutePathError: starts with `/` (or any absolute form)
            PathEscapeError: contains `..` or resolves outside `vault_root`
            ForbiddenZoneError: targets `.obsidian/`, `.git/`, etc.
            SymlinkEscapeError: an existing component is a symlink that escapes
        """
        cls._reject_basic_invalidity(raw)

        normalised = unicodedata.normalize("NFC", raw)
        relative = cls._build_relative(normalised, raw)
        cls._reject_forbidden_zones(relative, raw)

        try:
            vault_root_resolved = vault_root.resolve(strict=True)
        except FileNotFoundError as exc:
            raise InvalidPathError(f"vault root does not exist: {vault_root}") from exc

        cls._check_symlink_chain(vault_root_resolved, relative)
        cls._check_final_containment(vault_root_resolved, relative, raw)

        return cls(vault_root=vault_root_resolved, relative=relative)

    @staticmethod
    def _reject_basic_invalidity(raw: str) -> None:
        if not isinstance(raw, str):
            raise InvalidPathError(
                f"path must be a string, got {type(raw).__name__}"
            )
        if len(raw) == 0:
            raise InvalidPathError("path is empty")
        if len(raw) > _MAX_PATH_LENGTH:
            raise InvalidPathError(
                f"path exceeds maximum length of {_MAX_PATH_LENGTH} chars"
            )
        if not raw.strip():
            raise InvalidPathError("path is whitespace only")
        if "\x00" in raw:
            raise InvalidPathError("path contains a null byte")

    @staticmethod
    def _build_relative(normalised: str, raw: str) -> PurePosixPath:
        # Reject absolute paths *before* building PurePosixPath so platform-specific
        # forms (`/x`, `\\?\C:`, etc.) never sneak through.
        if normalised.startswith("/") or normalised.startswith("\\"):
            raise AbsolutePathError(f"absolute paths are not allowed: {raw!r}")

        relative = PurePosixPath(normalised)
        if relative.is_absolute():  # pragma: no cover - defence in depth, the
            # leading-/ check above catches every PurePosixPath-absolute input.
            raise AbsolutePathError(f"absolute paths are not allowed: {raw!r}")

        parts = relative.parts
        if not parts:
            raise InvalidPathError(f"path has no significant segments: {raw!r}")
        if len(parts) > _MAX_SEGMENTS:
            raise InvalidPathError(
                f"path exceeds maximum {_MAX_SEGMENTS} segments"
            )
        for part in parts:
            if part == "..":
                raise PathEscapeError(f"path contains a '..' segment: {raw!r}")
            if len(part.encode("utf-8")) > _MAX_SEGMENT_BYTES:
                raise InvalidPathError(
                    f"segment exceeds {_MAX_SEGMENT_BYTES} bytes: {part!r}"
                )
        return relative

    @staticmethod
    def _reject_forbidden_zones(relative: PurePosixPath, raw: str) -> None:
        first = relative.parts[0]
        if first in _FORBIDDEN_DIR_PREFIXES:
            raise ForbiddenZoneError(
                f"path targets a forbidden directory: {raw!r}"
            )
        if first in _FORBIDDEN_FILES and len(relative.parts) == 1:
            raise ForbiddenZoneError(f"path targets the config file: {raw!r}")

    @staticmethod
    def _check_symlink_chain(
        vault_root_resolved: Path, relative: PurePosixPath
    ) -> None:
        """Walk components; for each that exists and is a symlink, verify its
        resolved target stays under `vault_root_resolved`.

        We do this BEFORE the final containment check so escaping symlinks
        produce `SymlinkEscapeError` (more diagnostic) rather than the generic
        `PathEscapeError`.
        """
        cursor = vault_root_resolved
        for part in relative.parts:
            cursor = cursor / part
            try:
                is_link = cursor.is_symlink()
            except OSError as exc:  # pragma: no cover - defensive, e.g. EACCES
                raise InvalidPathError(
                    f"cannot stat path component {cursor}: {exc}"
                ) from exc
            if not cursor.exists() and not is_link:
                # Component does not exist — fine for create_note.
                # Nothing further down can be a symlink (the parent is missing).
                return
            if is_link:
                resolved_target = cursor.resolve(strict=False)
                try:
                    resolved_target.relative_to(vault_root_resolved)
                except ValueError as exc:
                    raise SymlinkEscapeError(
                        f"symlink {cursor} resolves outside vault: {resolved_target}"
                    ) from exc

    @staticmethod
    def _check_final_containment(
        vault_root_resolved: Path, relative: PurePosixPath, raw: str
    ) -> None:
        """Defence-in-depth: even after the symlink walk, ensure the fully
        resolved candidate lives under the vault root."""
        candidate = vault_root_resolved.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:  # pragma: no cover - defensive against rare ENOENT/EACCES
            raise InvalidPathError(
                f"cannot resolve path {candidate}: {exc}"
            ) from exc
        if resolved == vault_root_resolved:  # pragma: no cover - "." is caught earlier
            return
        try:
            resolved.relative_to(vault_root_resolved)
        except ValueError as exc:  # pragma: no cover - belt-and-suspenders;
            # symlink escape is caught in _check_symlink_chain, traversal in _build_relative.
            raise PathEscapeError(
                f"path resolves outside vault: {raw!r} -> {resolved}"
            ) from exc

    @property
    def absolute(self) -> Path:
        """Absolute path of this entry on disk (does not resolve symlinks)."""
        return self.vault_root.joinpath(*self.relative.parts)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"VaultPath({str(self.relative)!r})"
