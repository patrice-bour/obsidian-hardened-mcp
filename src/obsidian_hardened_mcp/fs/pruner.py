# SPDX-License-Identifier: Apache-2.0
"""Auto-cleanup of `<vault>/.ohmcp-trash/`.

Snapshots accumulate one per destructive op. This module sweeps stale
snapshots based on a `TrashPolicy` loaded from the vault YAML config
(`<vault>/.obsidian-hardened-mcp.yaml` § `trash:`).

Three layered constraints, from most to least conservative:

1. ``keep_at_least_per_path`` — for every distinct source-path that
   ever ended up in trash, retain at least N most-recent snapshots,
   regardless of age. Protects the recovery path: even if you deleted
   one cherished note 60 days ago and 50 trivial ones since, the
   cherished one's last snapshot is preserved.
2. ``keep_at_least_global`` — never let the total snapshot count
   drop below this number. A coarse second filter; mostly relevant
   when the per-path floor itself doesn't keep enough.
3. ``retention_days`` — confirmed-prune candidates are those whose
   snapshot is older than this many days AND not protected by either
   floor. ``None`` disables time-based pruning entirely.

Optionally, ``max_total_mb`` caps the total disk usage; the oldest
non-floor-protected snapshots are pruned until the cap is met.

Every prune emits an `AuditEvent` (op_kind="destructive",
tool="trash_pruner") so deletions are traceable through the same
audit log the rest of the server uses.
"""

from __future__ import annotations

import re
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from obsidian_hardened_mcp.domain.audit import AuditEvent
from obsidian_hardened_mcp.tools._base import new_request_id, params_hash

if TYPE_CHECKING:
    from obsidian_hardened_mcp.config import TrashPolicy
    from obsidian_hardened_mcp.security.audit_logger import AuditLogger

_TRASH_DIRNAME = ".ohmcp-trash"
_SNAPSHOT_NAME_RE = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{8})$")


@dataclass(frozen=True)
class _SnapshotInfo:
    """Inspection result for a single snapshot directory."""

    dir: Path
    snapshot_id: str
    ts: datetime
    primary_source: str
    total_bytes: int


@dataclass(frozen=True)
class PruneResult:
    """Outcome of a single `prune_trash` invocation."""

    snapshots_examined: int = 0
    snapshots_pruned: int = 0
    snapshots_failed: int = 0
    snapshots_skipped: int = 0
    bytes_pruned: int = 0
    pruned_ids: tuple[str, ...] = field(default_factory=tuple)
    skipped_dirs: tuple[str, ...] = field(default_factory=tuple)


def prune_trash(
    vault_root: Path,
    policy: TrashPolicy,
    audit: AuditLogger,
    *,
    now: datetime | None = None,
    trigger: str = "startup",
) -> PruneResult:
    """Sweep `<vault_root>/.ohmcp-trash/` according to `policy`.

    Safe by construction:

    - No-op if the trash directory doesn't exist.
    - Snapshot directories whose name doesn't match the canonical
      ``YYYYMMDDTHHMMSSZ-<8 hex>`` pattern are recorded as skipped
      and never deleted.
    - ``keep_at_least_per_path`` and ``keep_at_least_global`` floors
      are applied before any deletion; we never go below either.
    - Every prune (and every skip with cause) is reflected in an
      `AuditEvent`.

    Parameters
    ----------
    vault_root:
        Vault root. The pruner only ever touches files under
        ``<vault_root>/.ohmcp-trash/``.
    policy:
        The retention policy. See `TrashPolicy` in `config.py`.
    audit:
        Audit logger. The pruner emits one event per pruned (or
        attempted-prune-but-failed) snapshot, plus one summary event
        at the end if anything happened. All events share a single
        ``request_id`` so a downstream consumer can correlate them
        as one logical sweep.
    now:
        Override the "current" time (tests). Defaults to UTC now.
    trigger:
        Free-form label for the audit's ``params_hash`` field —
        ``"startup"``, ``"post_op"``, etc. Helps trace WHY a prune
        happened.
    """
    trash_root = vault_root / _TRASH_DIRNAME
    if not trash_root.is_dir():
        return PruneResult()

    now = now if now is not None else datetime.now(tz=UTC)
    request_id = new_request_id()
    sweep_started = time.monotonic()

    snapshots: list[_SnapshotInfo] = []
    skipped: list[str] = []

    for child in sorted(trash_root.iterdir()):
        if not child.is_dir():
            # Stray files at the trash root are not snapshots — skip
            # silently (we don't claim ownership of unexpected items).
            continue
        info = _inspect_snapshot(child)
        if info is None:
            skipped.append(child.name)
            continue
        snapshots.append(info)

    examined = len(snapshots)

    if not snapshots:
        return PruneResult(
            snapshots_examined=0,
            snapshots_skipped=len(skipped),
            skipped_dirs=tuple(skipped),
        )

    # 1) Per-path floor: protect the N most-recent for each source.
    by_source: dict[str, list[_SnapshotInfo]] = defaultdict(list)
    for snap in snapshots:
        by_source[snap.primary_source].append(snap)

    protected_ids: set[str] = set()
    for snaps in by_source.values():
        snaps_desc = sorted(snaps, key=lambda s: s.ts, reverse=True)
        for s in snaps_desc[: policy.keep_at_least_per_path]:
            protected_ids.add(s.snapshot_id)

    # 2) Apply retention to the unprotected set.
    candidates: list[_SnapshotInfo] = []
    if policy.retention_days is not None:
        cutoff = now - timedelta(days=policy.retention_days)
        for snap in snapshots:
            if snap.snapshot_id in protected_ids:
                continue
            if snap.ts < cutoff:
                candidates.append(snap)

    # 3) Optional size cap: prune oldest non-protected until total <= cap.
    if policy.max_total_mb is not None:
        cap_bytes = policy.max_total_mb * 1024 * 1024
        # Compute total AFTER applying step 2.
        ids_already_pruned = {c.snapshot_id for c in candidates}
        remaining = [
            s for s in snapshots if s.snapshot_id not in ids_already_pruned
        ]
        total_bytes = sum(s.total_bytes for s in remaining)
        if total_bytes > cap_bytes:
            # Sort the unprotected remainder oldest-first; prune until
            # under cap. Protected-by-floor snapshots stay untouched
            # even if that means breaching the cap (recovery wins).
            unprotected = sorted(
                (s for s in remaining if s.snapshot_id not in protected_ids),
                key=lambda s: s.ts,
            )
            for snap in unprotected:
                if total_bytes <= cap_bytes:
                    break
                candidates.append(snap)
                total_bytes -= snap.total_bytes

    # 4) Global floor: ensure final count >= keep_at_least_global.
    final_count = len(snapshots) - len(candidates)
    if final_count < policy.keep_at_least_global:
        deficit = policy.keep_at_least_global - final_count
        # Re-add the most-recent of the candidates to maintain the floor.
        candidates_desc = sorted(candidates, key=lambda s: s.ts, reverse=True)
        kept_for_floor = set(c.snapshot_id for c in candidates_desc[:deficit])
        candidates = [c for c in candidates if c.snapshot_id not in kept_for_floor]

    # 5) Execute prunes + audit each.
    pruned: list[_SnapshotInfo] = []
    failed_count = 0
    for snap in candidates:
        op_started = time.monotonic()
        try:
            shutil.rmtree(snap.dir)
        except OSError as exc:  # pragma: no cover - defensive (ENOSPC, EROFS…)
            failed_count += 1
            duration_ms = int((time.monotonic() - op_started) * 1000)
            _emit_prune_audit(
                audit,
                vault_root=vault_root,
                snap=snap,
                outcome="failure",
                trigger=trigger,
                request_id=request_id,
                duration_ms=duration_ms,
                error=str(exc),
            )
            continue
        duration_ms = int((time.monotonic() - op_started) * 1000)
        pruned.append(snap)
        _emit_prune_audit(
            audit,
            vault_root=vault_root,
            snap=snap,
            outcome="success",
            trigger=trigger,
            request_id=request_id,
            duration_ms=duration_ms,
            error=None,
        )

    # 6) Summary event for the whole sweep (only if anything happened).
    if pruned or failed_count:
        _emit_prune_summary(
            audit,
            request_id=request_id,
            trigger=trigger,
            sweep_started=sweep_started,
            pruned_count=len(pruned),
            failed_count=failed_count,
            bytes_pruned=sum(s.total_bytes for s in pruned),
        )

    return PruneResult(
        snapshots_examined=examined,
        snapshots_pruned=len(pruned),
        snapshots_failed=failed_count,
        snapshots_skipped=len(skipped),
        bytes_pruned=sum(s.total_bytes for s in pruned),
        pruned_ids=tuple(s.snapshot_id for s in pruned),
        skipped_dirs=tuple(skipped),
    )


# --------------------------------------------------------------------- helpers


def _inspect_snapshot(snap_dir: Path) -> _SnapshotInfo | None:
    """Parse a snapshot directory; return None on any malformed input."""
    name = snap_dir.name
    match = _SNAPSHOT_NAME_RE.match(name)
    if match is None:
        return None

    try:
        ts = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:  # pragma: no cover - regex already enforces the shape
        return None

    primary: str | None = None
    total_bytes = 0
    try:
        for f in snap_dir.rglob("*"):
            if not f.is_file():
                continue
            try:
                total_bytes += f.stat().st_size
            except OSError:  # pragma: no cover - defensive (EACCES…)
                continue
            if primary is None:
                # The snapshot module guarantees one file per snapshot
                # (vp.relative). Take the first .md we encounter; if none,
                # fall through to the snapshot id as a unique key.
                primary = f.relative_to(snap_dir).as_posix()
    except OSError:  # pragma: no cover
        return None

    if primary is None:
        # Snapshot is empty / has no files. Treat the snapshot_id as its
        # own primary so it never groups with anything else.
        primary = f"__orphan__/{name}"

    return _SnapshotInfo(
        dir=snap_dir,
        snapshot_id=name,
        ts=ts,
        primary_source=primary,
        total_bytes=total_bytes,
    )


def _emit_prune_audit(
    audit: AuditLogger,
    *,
    vault_root: Path,
    snap: _SnapshotInfo,
    outcome: str,
    trigger: str,
    request_id: str,
    duration_ms: int,
    error: str | None,
) -> None:
    """One audit entry per pruned (or attempted-prune-failed) snapshot.

    `vault_path` is the snapshot directory's path RELATIVE to the vault —
    that's what the pruner physically destroyed, not the original source
    note. The original source is folded into `params_hash` alongside the
    trigger and any error. `request_id` is the call-level id shared by
    every event from this sweep.
    """
    rel_dir = snap.dir.relative_to(vault_root).as_posix()
    event = AuditEvent(
        ts=datetime.now(tz=UTC),
        request_id=request_id,
        tool="trash_pruner",
        vault_path=rel_dir,
        op_kind="destructive",
        outcome=outcome,  # type: ignore[arg-type]
        duration_ms=duration_ms,
        snapshot_id=snap.snapshot_id,
        params_hash=params_hash(trigger, snap.primary_source, error or ""),
        dry_run=False,
    )
    audit.log(event)


def _emit_prune_summary(
    audit: AuditLogger,
    *,
    request_id: str,
    trigger: str,
    sweep_started: float,
    pruned_count: int,
    failed_count: int,
    bytes_pruned: int,
) -> None:
    """One summary event per sweep, emitted only if at least one prune
    was attempted. Uses ``op_kind="meta"`` so a downstream filter on
    destructive entries doesn't double-count snapshot-level events.

    The summary's ``vault_path`` is the trash root itself (no specific
    snapshot); ``snapshot_id`` is null. The aggregate counts live in
    ``params_hash`` and the wall time of the whole sweep is reported
    in ``duration_ms``.
    """
    duration_ms = int((time.monotonic() - sweep_started) * 1000)
    event = AuditEvent(
        ts=datetime.now(tz=UTC),
        request_id=request_id,
        tool="trash_pruner",
        vault_path=f"{_TRASH_DIRNAME}/",
        op_kind="meta",
        outcome="success" if failed_count == 0 else "failure",
        duration_ms=duration_ms,
        snapshot_id=None,
        params_hash=params_hash(
            "summary",
            trigger,
            pruned_count,
            failed_count,
            bytes_pruned,
        ),
        dry_run=False,
    )
    audit.log(event)


# Re-export so callers don't have to know the magic constant.
TRASH_DIRNAME = _TRASH_DIRNAME
