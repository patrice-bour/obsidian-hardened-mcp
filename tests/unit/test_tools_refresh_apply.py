"""refresh_apply — the sole auto-write path (vault-refresh v2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.frontmatter import parse_note
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.refresh import refresh_apply

TODAY = date(2026, 7, 6)


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


@pytest.fixture
def auto_note(tmp_vault: Path) -> Path:
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_tasks:\n  t1:\n    note: 01_Notes/auto.md\n    prompt: Do it.\n"
    )
    target = tmp_vault / "01_Notes" / "auto.md"
    target.write_text(
        "---\ntitle: Keep me\nrefresh_policy: auto\nrefresh_task: t1\n"
        "refresh_every: 1m\nrefresh_last: 2026-05-01\n---\nOld body\n"
    )
    return target


class TestPreconditions:
    def test_rejects_note_without_contract(
        self, tmp_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = refresh_apply(config, audit, "01_Notes/sample.md", "New", today=TODAY)
        assert not result.ok and result.error.code == ErrorCode.VALIDATION_FAILED

    def test_rejects_flag_policy(
        self, tmp_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        note = tmp_vault / "01_Notes" / "flagged.md"
        note.write_text(
            "---\nrefresh_every: 1m\nrefresh_last: 2026-05-01\n---\nBody\n"
        )
        result = refresh_apply(config, audit, "01_Notes/flagged.md", "New", today=TODAY)
        assert not result.ok and result.error.code == ErrorCode.VALIDATION_FAILED

    def test_rejects_unpinned_task(
        self, auto_note: Path, tmp_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        other = tmp_vault / "01_Notes" / "other-auto.md"
        other.write_text(
            "---\nrefresh_policy: auto\nrefresh_task: t1\n"
            "refresh_every: 1m\nrefresh_last: 2026-05-01\n---\nBody\n"
        )
        result = refresh_apply(
            config, audit, "01_Notes/other-auto.md", "New", today=TODAY
        )
        assert not result.ok and result.error.code == ErrorCode.VALIDATION_FAILED


class TestApply:
    def test_body_replaced_frontmatter_managed(
        self, auto_note: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        result = refresh_apply(
            config, audit, "01_Notes/auto.md", "# Fresh\n\nNew body\n", today=TODAY
        )
        assert result.ok
        parsed = parse_note(auto_note.read_text())
        assert parsed.body == "# Fresh\n\nNew body\n"
        fm = parsed.frontmatter
        assert str(fm["title"]) == "Keep me"                    # préservé
        assert str(fm["refresh_last"]) == "2026-07-06"          # avancé
        assert str(fm["refresh_due"]) == "2026-08-06"           # recalculé (1m)
        assert fm["refresh_stale"] is False
        assert result.data["snapshot_id"]

    def test_snapshot_written_to_trash(
        self, auto_note: Path, tmp_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        refresh_apply(config, audit, "01_Notes/auto.md", "New body\n", today=TODAY)
        snaps = list((tmp_vault / ".ohmcp-trash").rglob("*"))
        assert any(p.is_file() and "Old body" in p.read_text() for p in snaps)

    def test_audited_with_snapshot_id(
        self, auto_note: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        refresh_apply(config, audit, "01_Notes/auto.md", "New\n", today=TODAY)
        logs = "".join(p.read_text() for p in config.audit_dir.glob("*.jsonl"))
        assert '"tool":"refresh_apply"' in logs and '"snapshot_id":' in logs
