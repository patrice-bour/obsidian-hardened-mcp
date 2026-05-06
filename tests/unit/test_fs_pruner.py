"""Tests for `fs.pruner` — auto-cleanup of `.ohmcp-trash/`.

Covers the layered constraints (`retention_days`, per-path floor,
global floor, `max_total_mb`) and the audit emission contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import TrashPolicy
from obsidian_hardened_mcp.fs.pruner import prune_trash
from obsidian_hardened_mcp.security.audit_logger import AuditLogger

# --------------------------------------------------------------------- helpers


def _audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(audit_dir=tmp_path / "audit")


def _make_snapshot(
    trash_root: Path,
    *,
    ts: datetime,
    source_path: str,
    body: str = "x" * 16,
    suffix: str = "abcd1234",
) -> Path:
    """Create a snapshot dir whose name reflects ``ts`` and whose only
    file lives at ``source_path`` (relative to the snapshot dir)."""
    name = f"{ts.strftime('%Y%m%dT%H%M%SZ')}-{suffix}"
    snap_dir = trash_root / name
    target = snap_dir / source_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return snap_dir


def _read_audit_lines(tmp_path: Path) -> list[dict[str, object]]:
    audit_dir = tmp_path / "audit"
    if not audit_dir.exists():
        return []
    out: list[dict[str, object]] = []
    for log in sorted(audit_dir.glob("*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


# ------------------------------------------------------------------------- 1.


class TestEmptyTrash:
    def test_no_op_when_trash_dir_missing(self, tmp_vault: Path, tmp_path: Path) -> None:
        # tmp_vault includes .ohmcp-trash by default; remove it to test
        (tmp_vault / ".ohmcp-trash").rmdir()
        result = prune_trash(tmp_vault, TrashPolicy(), _audit(tmp_path))
        assert result.snapshots_examined == 0
        assert result.snapshots_pruned == 0
        assert _read_audit_lines(tmp_path) == []

    def test_no_op_when_trash_dir_empty(self, tmp_vault: Path, tmp_path: Path) -> None:
        result = prune_trash(tmp_vault, TrashPolicy(), _audit(tmp_path))
        assert result.snapshots_examined == 0
        assert result.snapshots_pruned == 0


# ------------------------------------------------------------------------- 2.


class TestRetentionAge:
    def test_recent_snapshots_kept(self, tmp_vault: Path, tmp_path: Path) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        for i, suffix in enumerate(("aaaaaaaa", "bbbbbbbb", "cccccccc")):
            _make_snapshot(
                trash,
                ts=now - timedelta(days=i),  # all within 30 days
                source_path=f"notes/n{i}.md",
                suffix=suffix,
            )

        result = prune_trash(
            tmp_vault, TrashPolicy(retention_days=30), _audit(tmp_path), now=now
        )
        assert result.snapshots_examined == 3
        assert result.snapshots_pruned == 0
        # All three dirs still on disk
        assert sum(1 for _ in trash.iterdir()) == 3

    def test_old_snapshots_pruned_keeping_per_path_floor(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        # Three snapshots, ALL older than retention, ALL same source path.
        # With keep_at_least_per_path=1, the most recent of the three
        # must survive.
        _make_snapshot(
            trash, ts=now - timedelta(days=120), source_path="notes/a.md", suffix="11111111"
        )
        _make_snapshot(
            trash, ts=now - timedelta(days=90), source_path="notes/a.md", suffix="22222222"
        )
        most_recent_dir = _make_snapshot(
            trash, ts=now - timedelta(days=60), source_path="notes/a.md", suffix="33333333"
        )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(
                retention_days=30,
                keep_at_least_per_path=1,
                keep_at_least_global=0,  # disable global floor for this test
            ),
            _audit(tmp_path),
            now=now,
        )
        assert result.snapshots_examined == 3
        assert result.snapshots_pruned == 2
        # The most-recent snapshot for source notes/a.md must remain.
        assert most_recent_dir.exists()


# ------------------------------------------------------------------------- 3.


class TestGlobalFloor:
    def test_global_floor_overrides_retention(self, tmp_vault: Path, tmp_path: Path) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        # 10 snapshots, all old, all distinct source paths.
        # keep_at_least_per_path=1 protects 10 of them (one each), so the
        # global floor isn't tested by this case alone — instead use a
        # case where per-path doesn't fully protect.
        for i in range(10):
            _make_snapshot(
                trash,
                ts=now - timedelta(days=60 + i),
                source_path="notes/shared.md",  # SAME source for all
                suffix=f"{i:08x}",
            )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(
                retention_days=30,
                keep_at_least_per_path=1,  # protects 1
                keep_at_least_global=5,  # but floor demands 5 total
            ),
            _audit(tmp_path),
            now=now,
        )
        # Per-path protects 1 (most recent), retention would prune the 9
        # remainder, but global floor at 5 means we can only prune
        # (10 - 5) = 5 of those. So 5 pruned, 5 kept.
        assert result.snapshots_examined == 10
        assert result.snapshots_pruned == 5
        assert sum(1 for _ in trash.iterdir()) == 5


# ------------------------------------------------------------------------- 4.


class TestPerPathFloor:
    def test_protects_one_snapshot_per_distinct_source(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        # 6 snapshots, all old, 6 distinct source paths → per-path floor
        # at 1 should keep all six. Global floor at 0 doesn't kick in.
        for i in range(6):
            _make_snapshot(
                trash,
                ts=now - timedelta(days=60),
                source_path=f"notes/n{i}.md",
                suffix=f"{i:08x}",
            )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(
                retention_days=30,
                keep_at_least_per_path=1,
                keep_at_least_global=0,
            ),
            _audit(tmp_path),
            now=now,
        )
        assert result.snapshots_examined == 6
        assert result.snapshots_pruned == 0


# ------------------------------------------------------------------------- 5.


class TestMalformedNames:
    def test_invalid_snapshot_name_skipped(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        trash = tmp_vault / ".ohmcp-trash"
        weird = trash / "not-a-snapshot-dir"
        weird.mkdir()
        (weird / "stray.txt").write_text("hi")

        result = prune_trash(tmp_vault, TrashPolicy(), _audit(tmp_path))
        # The malformed dir must NOT be deleted.
        assert weird.exists()
        assert result.snapshots_skipped == 1
        assert "not-a-snapshot-dir" in result.skipped_dirs

    def test_stray_file_at_trash_root_ignored(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        trash = tmp_vault / ".ohmcp-trash"
        (trash / "README.txt").write_text("don't touch me")
        result = prune_trash(tmp_vault, TrashPolicy(), _audit(tmp_path))
        assert (trash / "README.txt").exists()
        assert result.snapshots_examined == 0


# ------------------------------------------------------------------------- 6.


class TestMaxTotalMb:
    def test_size_cap_prunes_oldest_unprotected(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        # 5 snapshots ALL pointing at the SAME source path, ~1MB each.
        # Per-path floor (1) protects only the most recent; the other 4
        # are unprotected and eligible for size-cap eviction.
        body = "x" * (1024 * 1024)  # 1 MB
        for i in range(5):
            _make_snapshot(
                trash,
                ts=now - timedelta(days=i),  # i=0 is most recent
                source_path="notes/shared.md",
                body=body,
                suffix=f"{i:08x}",
            )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(
                retention_days=None,  # no time pruning
                keep_at_least_per_path=1,  # protects only the most recent
                keep_at_least_global=0,
                max_total_mb=2,  # cap at 2 MB total
            ),
            _audit(tmp_path),
            now=now,
        )
        # 4 unprotected old snapshots, ~1 MB each, must shrink to under
        # 2 MB total (the protected one already counts ~1 MB), so the
        # cap forces ~3-4 evictions.
        assert result.snapshots_pruned >= 2

    def test_size_cap_respects_per_path_floor(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        """When every snapshot is per-path-protected, the cap is breached
        rather than violating recovery — by design (see pruner.py)."""
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        body = "x" * (1024 * 1024)  # 1 MB
        for i in range(5):
            _make_snapshot(
                trash,
                ts=now - timedelta(days=i),
                source_path=f"notes/distinct-{i}.md",  # distinct sources
                body=body,
                suffix=f"{i:08x}",
            )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(
                retention_days=None,
                keep_at_least_per_path=1,  # protects ALL 5 (one each)
                keep_at_least_global=0,
                max_total_mb=2,  # cap below total size
            ),
            _audit(tmp_path),
            now=now,
        )
        assert result.snapshots_pruned == 0

    def test_no_size_cap_keeps_everything(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        for i in range(3):
            _make_snapshot(
                trash,
                ts=now - timedelta(days=i),
                source_path=f"notes/n{i}.md",
                suffix=f"{i:08x}",
            )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(retention_days=30),  # all recent, max_total_mb=None
            _audit(tmp_path),
            now=now,
        )
        assert result.snapshots_pruned == 0


# ------------------------------------------------------------------------- 7.


class TestAuditEmission:
    def test_each_prune_emits_audit_entry(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        # Two old snapshots, distinct sources, per-path floor = 0 so
        # both get pruned.
        _make_snapshot(
            trash, ts=now - timedelta(days=60), source_path="notes/a.md", suffix="aaaaaaaa"
        )
        _make_snapshot(
            trash, ts=now - timedelta(days=60), source_path="notes/b.md", suffix="bbbbbbbb"
        )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(
                retention_days=30,
                keep_at_least_per_path=0,
                keep_at_least_global=0,
            ),
            _audit(tmp_path),
            now=now,
            trigger="startup",
        )
        assert result.snapshots_pruned == 2

        entries = _read_audit_lines(tmp_path)
        assert len(entries) == 2
        for entry in entries:
            assert entry["tool"] == "trash_pruner"
            assert entry["op_kind"] == "destructive"
            assert entry["outcome"] == "success"
            # vault_path is the snapshot dir relative to vault root
            assert isinstance(entry["vault_path"], str)
            assert entry["vault_path"].startswith(".ohmcp-trash/")
            # params_hash carries the trigger label and the source path
            params_hash = entry["params_hash"]
            assert isinstance(params_hash, str)
            assert "startup|" in params_hash


# ------------------------------------------------------------------------- 8.


class TestRetentionDisabled:
    def test_null_retention_means_no_time_pruning(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        now = datetime(2026, 5, 6, tzinfo=UTC)
        trash = tmp_vault / ".ohmcp-trash"
        for i in range(3):
            _make_snapshot(
                trash,
                ts=now - timedelta(days=365),  # very old
                source_path=f"notes/n{i}.md",
                suffix=f"{i:08x}",
            )

        result = prune_trash(
            tmp_vault,
            TrashPolicy(
                retention_days=None,  # disable
                keep_at_least_per_path=0,
                keep_at_least_global=0,
                max_total_mb=None,
            ),
            _audit(tmp_path),
            now=now,
        )
        # Nothing time-eligible, no size cap → no prunes regardless of age.
        assert result.snapshots_pruned == 0
        assert sum(1 for _ in trash.iterdir()) == 3


# ------------------------------------------------------------------------- 9.


class TestPolicyDefaults:
    def test_default_policy_is_documented_values(self) -> None:
        p = TrashPolicy()
        assert p.retention_days == 30
        assert p.keep_at_least_per_path == 1
        assert p.keep_at_least_global == 5
        assert p.max_total_mb is None

    def test_negative_retention_rejected(self) -> None:
        with pytest.raises(ValueError, match="retention_days"):
            TrashPolicy(retention_days=-1)

    def test_negative_keep_at_least_rejected(self) -> None:
        with pytest.raises(ValueError, match="keep_at_least"):
            TrashPolicy(keep_at_least_per_path=-1)
        with pytest.raises(ValueError, match="keep_at_least"):
            TrashPolicy(keep_at_least_global=-1)

    def test_zero_max_total_mb_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_total_mb"):
            TrashPolicy(max_total_mb=0)
