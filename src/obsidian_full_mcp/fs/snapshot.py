# SPDX-License-Identifier: Apache-2.0
"""Pre-destruction file snapshots.

Before any destructive op (`delete_note`, `rename_note`, `move_note`)
mutates the vault, the original file is copied into the vault's
`.ofmcp-trash/<UTC-ts>-<short-hash>/<original-relative-path>`. The
snapshot is best-effort: if the copy fails, the destructive call aborts
without touching the source.

`.ofmcp-trash/` is in the VaultPath sandbox's forbidden zones, so MCP
read tools cannot expose snapshots back to clients. Restoration is a
manual / out-of-band operation in v0.1 (tracked as a v0.2 followup).

We only snapshot **single files** in M6. Directory ops are out of
scope; passing a directory raises `SnapshotError`.
"""

from __future__ import annotations

import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

from obsidian_full_mcp.domain.vault_path import VaultPath


class SnapshotError(Exception):
    """A pre-destruction snapshot could not be created."""


def _new_snapshot_id() -> str:
    """`YYYYMMDDTHHMMSSZ-<8 hex chars>` — UTC + random suffix.

    The hex suffix guarantees uniqueness across rapid successive calls
    within the same second (when timestamps would otherwise collide).
    """
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(4)}"


def snapshot_for_destruction(
    vp: VaultPath, *, snapshot_root: Path
) -> str:
    """Copy `vp` into `snapshot_root/<snapshot_id>/<vp.relative>` and return
    the snapshot id.

    Args:
        vp: validated `VaultPath` of the file to snapshot.
        snapshot_root: directory under which the snapshot tree is written
            (typically `<vault_root>/.ofmcp-trash`).

    Raises:
        SnapshotError: source file is missing, is not a regular file, or the
            copy itself fails (e.g. ENOSPC). The caller MUST treat this as a
            hard abort and not perform the destructive op.
    """
    source = vp.absolute
    if not source.exists():
        raise SnapshotError(f"source file does not exist: {vp.relative}")
    if not source.is_file():
        raise SnapshotError(
            f"snapshot only supports regular files: {vp.relative} is not"
        )

    snapshot_id = _new_snapshot_id()
    if not vp.relative.parts:  # pragma: no cover - VaultPath enforces non-empty
        raise SnapshotError(
            "VaultPath.relative is empty; cannot build snapshot destination"
        )
    destination = snapshot_root / snapshot_id / Path(*vp.relative.parts)

    # Defense in depth: confirm the resolved destination stays under
    # `snapshot_root`. VaultPath already rejects traversal upstream, but
    # asserting here would catch any future bypass before we copy data.
    resolved_dest = (
        destination.parent.resolve(strict=False) / destination.name
    )
    if not resolved_dest.is_relative_to(snapshot_root.resolve(strict=False)):
        raise SnapshotError(
            f"snapshot destination escapes snapshot_root: {resolved_dest}"
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    except OSError as exc:
        raise SnapshotError(
            f"failed to snapshot {vp.relative}: {exc}"
        ) from exc
    return snapshot_id
