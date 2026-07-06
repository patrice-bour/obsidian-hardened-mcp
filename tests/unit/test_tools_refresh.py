"""Tests for the list_stale_notes tool (vault-refresh v1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.frontmatter import parse_note as _parse_note
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools import refresh as _refresh_module
from obsidian_hardened_mcp.tools.refresh import list_stale_notes
from obsidian_hardened_mcp.validation.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
)

TODAY = date(2026, 7, 6)


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


def _write(tmp_vault: Path, rel: str, text: str) -> Path:
    target = tmp_vault / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


@pytest.fixture
def seeded_vault(tmp_vault: Path) -> Path:
    _write(
        tmp_vault,
        "01_Notes/stale-flag.md",
        "---\nrefresh_every: 1m\nrefresh_last: 2026-05-01\n"
        'refresh_prompt: "Re-check prices."\n---\nBody\n',
    )
    _write(
        tmp_vault,
        "01_Notes/fresh.md",
        "---\nrefresh_every: 1y\nrefresh_last: 2026-07-01\n---\nBody\n",
    )
    _write(
        tmp_vault,
        "01_Notes/stale-on-read.md",
        "---\nrefresh_policy: on_read\nrefresh_every: 7d\n"
        "refresh_last: 2026-06-01\n---\nBody\n",
    )
    _write(
        tmp_vault,
        "01_Notes/broken-contract.md",
        "---\nrefresh_every: 1x\nrefresh_last: 2026-06-01\n---\nBody\n",
    )
    return tmp_vault


class TestScan:
    def test_reports_stale_and_counts(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, today=TODAY)
        assert result.ok
        data = result.data
        assert data["with_contract"] == 3  # broken-contract est une anomalie
        assert data["marked"] == 0
        stale_paths = {entry["path"] for entry in data["stale"]}
        assert stale_paths == {"01_Notes/stale-flag.md", "01_Notes/stale-on-read.md"}

    def test_stale_entry_shape(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, today=TODAY)
        entry = next(
            e for e in result.data["stale"] if e["path"] == "01_Notes/stale-flag.md"
        )
        assert entry == {
            "path": "01_Notes/stale-flag.md",
            "policy": "flag",
            "last": "2026-05-01",
            "due": "2026-06-01",
            "days_overdue": 35,
            "prompt": "Re-check prices.",
            "task": None,
            "executable": False,
        }

    def test_anomaly_reported_scan_continues(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, today=TODAY)
        anomalies = result.data["anomalies"]
        assert len(anomalies) == 1
        assert anomalies[0]["path"] == "01_Notes/broken-contract.md"
        assert "refresh_every" in anomalies[0]["reason"]

    def test_policy_filter(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, policy="on_read", today=TODAY)
        assert [e["path"] for e in result.data["stale"]] == [
            "01_Notes/stale-on-read.md"
        ]

    def test_unknown_policy_fails(
        self, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, policy="yolo", today=TODAY)
        assert not result.ok
        assert result.error is not None and result.error.code == ErrorCode.VALIDATION_FAILED

    def test_scan_writes_nothing(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        before = {
            p: p.read_text() for p in seeded_vault.rglob("*.md")
        }
        list_stale_notes(config, audit, today=TODAY)
        after = {p: p.read_text() for p in seeded_vault.rglob("*.md")}
        assert before == after


class TestMark:
    def test_mark_stamps_due_and_stale(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, mark=True, today=TODAY)
        assert result.ok
        assert result.data["marked"] == 3  # les 3 notes sous contrat valides
        fm = _parse_note(
            (seeded_vault / "01_Notes" / "stale-flag.md").read_text()
        ).frontmatter
        assert str(fm["refresh_due"]) == "2026-06-01"
        assert fm["refresh_stale"] is True
        fm_fresh = _parse_note(
            (seeded_vault / "01_Notes" / "fresh.md").read_text()
        ).frontmatter
        assert str(fm_fresh["refresh_due"]) == "2027-07-01"
        assert fm_fresh["refresh_stale"] is False

    def test_mark_preserves_other_fields_and_body(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        target = seeded_vault / "01_Notes" / "stale-flag.md"
        list_stale_notes(config, audit, mark=True, today=TODAY)
        text = target.read_text()
        assert 'refresh_prompt: "Re-check prices."' in text
        assert text.rstrip().endswith("Body")

    def test_mark_is_idempotent(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        list_stale_notes(config, audit, mark=True, today=TODAY)
        snapshot = {
            p: p.read_text() for p in seeded_vault.rglob("*.md")
        }
        second = list_stale_notes(config, audit, mark=True, today=TODAY)
        assert second.data["marked"] == 0  # rien à réécrire
        assert {p: p.read_text() for p in seeded_vault.rglob("*.md")} == snapshot

    def test_mark_never_touches_broken_notes(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        broken = seeded_vault / "01_Notes" / "broken-contract.md"
        before = broken.read_text()
        list_stale_notes(config, audit, mark=True, today=TODAY)
        assert broken.read_text() == before

    def test_mark_writes_are_audited(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        list_stale_notes(config, audit, mark=True, today=TODAY)
        logs = list(config.audit_dir.glob("*.jsonl"))
        assert logs, "mark=True must leave an audit trail"
        content = "".join(p.read_text() for p in logs)
        assert "merge_frontmatter" in content


class _RejectAllHook:
    """Test-only hook that rejects every write, to prove `list_stale_notes`
    threads its `hooks` param down into `merge_frontmatter` and surfaces
    the resulting failure as an anomaly rather than a silent no-op."""

    name = "reject_all"
    phase = "pre_write"

    def validate(self, ctx: HookContext) -> HookResult:
        return HookResult.reject("test hook rejects all writes")


class TestMarkWithHooks:
    def test_rejecting_hook_blocks_mark_and_reports_anomaly(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        hooks = HookRegistry([_RejectAllHook()])
        result = list_stale_notes(config, audit, mark=True, today=TODAY, hooks=hooks)
        assert result.ok
        assert result.data["marked"] == 0
        anomalies = result.data["anomalies"]
        mark_failures = [a for a in anomalies if "mark failed" in a["reason"]]
        assert {a["path"] for a in mark_failures} == {
            "01_Notes/stale-flag.md",
            "01_Notes/fresh.md",
            "01_Notes/stale-on-read.md",
        }
        # No frontmatter was actually written.
        fm = _parse_note(
            (seeded_vault / "01_Notes" / "stale-flag.md").read_text()
        ).frontmatter
        assert "refresh_due" not in fm

    def test_empty_registry_still_marks(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        hooks = HookRegistry([])
        result = list_stale_notes(config, audit, mark=True, today=TODAY, hooks=hooks)
        assert result.ok
        assert result.data["marked"] == 3
        # broken-contract.md still reports its parse anomaly; no mark failures.
        assert not any("mark failed" in a["reason"] for a in result.data["anomalies"])


class TestHugeMagnitudeAnomaly:
    def test_huge_refresh_every_is_anomaly_not_abort(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        # Reproduces the OverflowError previously raised by compute_due()
        # OUTSIDE the per-note try/except, which used to abort the whole
        # scan. It must now be caught at parse_contract time (parse_interval
        # bounds the magnitude) and land in anomalies, with the rest of the
        # scan unaffected.
        _write(
            seeded_vault,
            "01_Notes/huge-interval.md",
            "---\nrefresh_every: 99999999d\nrefresh_last: 2026-06-01\n---\nBody\n",
        )
        result = list_stale_notes(config, audit, today=TODAY)
        assert result.ok
        data = result.data
        stale_paths = {entry["path"] for entry in data["stale"]}
        assert stale_paths == {"01_Notes/stale-flag.md", "01_Notes/stale-on-read.md"}
        anomaly_paths = {a["path"] for a in data["anomalies"]}
        assert "01_Notes/huge-interval.md" in anomaly_paths
        assert "01_Notes/broken-contract.md" in anomaly_paths


class TestMarkSingleRead:
    def test_note_vanishing_before_mark_is_anomaly_not_abort(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        # Reproduces: a note deleted between the scan read and the (former)
        # second read in _mark_note used to raise unguarded and abort the
        # whole scan. _mark_note now reuses the frontmatter parsed during
        # the scan and never re-reads the file itself; a vanished file is
        # only ever surfaced by merge_frontmatter's own guarded read.
        target = seeded_vault / "01_Notes" / "stale-flag.md"
        original = target.read_text()

        real_merge_frontmatter = _refresh_module.merge_frontmatter

        def _delete_then_merge(
            config: AppConfig,
            audit: AuditLogger,
            rel: str,
            *args: object,
            **kwargs: object,
        ) -> object:
            if rel == "01_Notes/stale-flag.md":
                target.unlink()
            return real_merge_frontmatter(config, audit, rel, *args, **kwargs)

        _refresh_module.merge_frontmatter = _delete_then_merge
        try:
            result = list_stale_notes(config, audit, mark=True, today=TODAY)
        finally:
            _refresh_module.merge_frontmatter = real_merge_frontmatter
            if not target.exists():
                target.write_text(original)

        assert result.ok
        anomalies = result.data["anomalies"]
        mark_failures = {a["path"]: a["reason"] for a in anomalies if "mark failed" in a["reason"]}
        assert "01_Notes/stale-flag.md" in mark_failures
        assert "not_found" in mark_failures["01_Notes/stale-flag.md"]
        # The other contracted notes were still marked normally.
        assert result.data["marked"] == 2


class TestVaultRootUnavailable:
    def test_missing_vault_root_returns_not_found(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        import shutil

        shutil.rmtree(seeded_vault)
        result = list_stale_notes(config, audit, today=TODAY)
        assert not result.ok
        assert result.error is not None
        assert result.error.code == ErrorCode.NOT_FOUND
        assert "vault root unavailable" in result.error.message


class TestAutoResolution:
    @pytest.fixture
    def auto_vault(self, tmp_vault: Path) -> Path:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "refresh_tasks:\n"
            "  goodtask:\n"
            "    note: 01_Notes/auto-ok.md\n"
            "    prompt: Rebuild the table.\n"
        )
        _write(
            tmp_vault,
            "01_Notes/auto-ok.md",
            "---\nrefresh_policy: auto\nrefresh_task: goodtask\n"
            "refresh_every: 7d\nrefresh_last: 2026-06-01\n---\nBody\n",
        )
        _write(
            tmp_vault,
            "01_Notes/auto-orphan.md",
            "---\nrefresh_policy: auto\nrefresh_task: nosuch\n"
            "refresh_every: 7d\nrefresh_last: 2026-06-01\n---\nBody\n",
        )
        _write(
            tmp_vault,
            "01_Notes/auto-hijack.md",
            "---\nrefresh_policy: auto\nrefresh_task: goodtask\n"
            "refresh_every: 7d\nrefresh_last: 2026-06-01\n---\nBody\n",
        )
        return tmp_vault

    def test_pinned_task_is_executable(
        self, auto_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, today=TODAY)
        entry = next(
            e for e in result.data["stale"] if e["path"] == "01_Notes/auto-ok.md"
        )
        assert entry["task"] == "goodtask" and entry["executable"] is True

    def test_unknown_task_is_anomaly_not_executable(
        self, auto_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, today=TODAY)
        entry = next(
            e for e in result.data["stale"] if e["path"] == "01_Notes/auto-orphan.md"
        )
        assert entry["executable"] is False
        assert any(
            a["path"] == "01_Notes/auto-orphan.md" and "unknown refresh_task" in a["reason"]
            for a in result.data["anomalies"]
        )

    def test_retargeting_is_blocked(
        self, auto_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, today=TODAY)
        entry = next(
            e for e in result.data["stale"] if e["path"] == "01_Notes/auto-hijack.md"
        )
        assert entry["executable"] is False
        assert any(
            "task/note mismatch" in a["reason"] for a in result.data["anomalies"]
        )

    def test_flag_notes_not_executable(
        self, seeded_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = list_stale_notes(config, audit, today=TODAY)
        assert all(
            e["executable"] is False and e["task"] is None
            for e in result.data["stale"]
        )
