"""Tests for the list_stale_notes tool (vault-refresh v1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.refresh import list_stale_notes

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
