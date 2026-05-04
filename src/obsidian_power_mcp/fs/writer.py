"""Atomic filesystem writes.

Writes go to a temporary file in the same directory, are flushed and fsynced,
then renamed (atomic on POSIX) into place. The directory itself is fsynced
afterwards so the rename survives a crash.

Same-directory tmp file is required: `os.replace` is only atomic when source
and destination are on the same filesystem.

Failures during write delete the tmp file so the directory is never left
with stray garbage. The caller never sees a half-written target.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from obsidian_power_mcp.domain.vault_path import VaultPath


class WriterError(Exception):
    """Base for writer errors."""


class AlreadyExistsError(WriterError):
    """Target exists and `exclusive=True` was requested."""


def atomic_write_text(
    path: VaultPath,
    content: str,
    *,
    exclusive: bool = False,
) -> None:
    """Write `content` to the file pointed to by `path` atomically.

    Args:
        path: validated `VaultPath`. Parent directories are created if needed.
        content: text to write, encoded as UTF-8.
        exclusive: if True, fail with `AlreadyExistsError` when the target
            already exists. (Used by `create_note`.)

    Atomicity:
        tmp_in_same_dir + write + flush + fsync + os.replace + dir-fsync
    """
    target = path.absolute
    target_dir = target.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    if exclusive and target.exists():
        raise AlreadyExistsError(f"file already exists: {path.relative}")

    tmp_path = _make_tmp_path(target)
    try:
        # Use os.open + fdopen for fine control over fsync.
        fd = os.open(
            tmp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(content)
                fp.flush()
                os.fsync(fp.fileno())
        except BaseException:
            _silent_unlink(tmp_path)
            raise

        # `os.replace` is the explicit atomic-rename API; ruff's PTH105 hint
        # to use `Path.replace` is the same syscall under the hood — keep the
        # `os.` form so the atomicity contract reads clearly.
        os.replace(tmp_path, target)  # noqa: PTH105
        _fsync_directory(target_dir)
    except BaseException:
        # Make sure the tmp file is gone if anything failed before rename.
        if tmp_path.exists():
            _silent_unlink(tmp_path)
        raise


def _make_tmp_path(target: Path) -> Path:
    """Build a unique tmp filename in the SAME directory as the target."""
    nonce = secrets.token_hex(4)
    return target.parent / f".{target.name}.tmp.{os.getpid()}.{nonce}"


def _fsync_directory(directory: Path) -> None:
    """fsync the directory so the rename is durable across crashes.

    macOS / APFS supports `O_DIRECTORY`. On systems where this fails we
    swallow the error — the rename itself is already atomic, the dir-sync
    is belt-and-suspenders for crash durability.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - very rare on macOS/Linux
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - non-POSIX filesystem
        pass
    finally:
        os.close(fd)


def _silent_unlink(path: Path) -> None:
    import contextlib

    with contextlib.suppress(FileNotFoundError):
        path.unlink()
