"""Filesystem read operations.

Read functions accept already-validated `VaultPath` objects only.
They never see raw user input.

iCloud awareness: macOS iCloud Drive offloads files by replacing the contents
with a metadata stub stored as `.<basename>.icloud` next to the original path.
A read against an offloaded file raises `FileOffloadedError` with a hint to
materialise it via `brctl download`.
"""

from __future__ import annotations

from obsidian_power_mcp.domain.vault_path import VaultPath

DEFAULT_MAX_SIZE_BYTES = 10 * 1024 * 1024


class FsError(Exception):
    """Base for filesystem read errors."""


class NotFoundError(FsError):
    """Path does not exist."""


class NotAFileError(FsError):
    """Path exists but is not a regular file."""


class FileTooLargeError(FsError):
    """File exceeds the configured size limit."""


class FileOffloadedError(FsError):
    """File is iCloud-offloaded and not currently materialised on disk."""


def read_text(
    path: VaultPath, *, max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
) -> str:
    """Read a file as UTF-8 text.

    Raises:
        NotFoundError: file does not exist
        NotAFileError: path is a directory or other non-file entry
        FileTooLargeError: file > `max_size_bytes`
        FileOffloadedError: file is iCloud-offloaded
    """
    target = path.absolute

    # iCloud offloaded check first: the stub is sibling, suffixed `.icloud`,
    # with the original basename prefixed by a dot.
    icloud_stub = target.parent / f".{target.name}.icloud"
    if icloud_stub.exists():
        raise FileOffloadedError(
            f"{path.relative} is iCloud-offloaded; run "
            f"`brctl download {target}` to materialise it"
        )

    if not target.exists():
        raise NotFoundError(f"file not found: {path.relative}")
    if not target.is_file():
        raise NotAFileError(f"not a regular file: {path.relative}")

    size = target.stat().st_size
    if size > max_size_bytes:
        raise FileTooLargeError(
            f"{path.relative} is {size} bytes, exceeds limit {max_size_bytes}"
        )

    return target.read_text(encoding="utf-8")
