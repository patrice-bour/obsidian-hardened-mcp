"""refresh_apply — the sole auto-write path (vault-refresh v2)."""

from __future__ import annotations

import sys
import unicodedata
from datetime import date
from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.frontmatter import parse_note
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.refresh import refresh_apply
from obsidian_hardened_mcp.validation.hooks import HookContext, HookRegistry, HookResult

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


class TestUnicodeNormalization:
    @pytest.mark.skipif(
        sys.platform != "darwin",
        reason="requires normalization-insensitive filesystem lookup (APFS)",
    )
    def test_accented_filename_nfd_on_disk_pins_against_nfc_whitelist(
        self, tmp_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        # Regression: mirrors TestAutoResolution's scan-side test in
        # test_tools_refresh.py. The note is stored NFD on disk (as a
        # macOS/iCloud-synced accented filename can be); the whitelist's
        # `note:` is typed/stored NFC (`parse_refresh_task` normalizes it).
        # Before the fix, `refresh_apply`'s `rel = str(vp.relative)` (NFC,
        # via `VaultPath`) was already NFC — so this specific comparison
        # worked — but the scan side fed the executor the raw NFD `rel`,
        # which then failed the `tasks[task_id].note == rel` pinning check
        # here. This exercises the full pinned round trip end-to-end.
        nfc_rel = "01_Notes/Paysage modèles.md"
        nfd_name = unicodedata.normalize("NFD", "Paysage modèles.md")
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "refresh_tasks:\n"
            "  accented:\n"
            f"    note: {nfc_rel}\n"
            "    prompt: Rebuild the accented note.\n"
        )
        target = tmp_vault / "01_Notes" / nfd_name
        target.write_text(
            "---\nrefresh_policy: auto\nrefresh_task: accented\n"
            "refresh_every: 1m\nrefresh_last: 2026-05-01\n---\nOld body\n"
        )
        result = refresh_apply(config, audit, nfc_rel, "New body\n", today=TODAY)
        assert result.ok, result.error
        parsed = parse_note(target.read_text())
        assert parsed.body == "New body\n"
        assert str(parsed.frontmatter["refresh_last"]) == "2026-07-06"


class TestHooksBeforeSnapshot:
    """Guards the hooks-before-snapshot invariant: a hook rejection must
    have ZERO side effects (no write, no snapshot, no success audit line).
    A future refactor moving the snapshot earlier would silently break this
    without a test to catch it."""

    class _RejectAllHook:
        name = "reject_all"
        phase = "pre_write"

        def validate(self, ctx: HookContext) -> HookResult:
            return HookResult.reject("test hook rejects all writes")

    def test_hook_rejection_has_zero_side_effects(
        self, auto_note: Path, tmp_vault: Path, config: AppConfig, audit: AuditLogger
    ) -> None:
        before = auto_note.read_text()
        hooks = HookRegistry([self._RejectAllHook()])

        result = refresh_apply(
            config, audit, "01_Notes/auto.md", "New body\n", today=TODAY, hooks=hooks
        )

        assert not result.ok
        assert result.error is not None
        assert result.error.code == ErrorCode.VALIDATION_FAILED

        # Note untouched.
        assert auto_note.read_text() == before

        # No snapshot taken.
        trash = tmp_vault / ".ohmcp-trash"
        snapshot_files = [p for p in trash.rglob("*") if p.is_file()]
        assert snapshot_files == []

        # No refresh_apply success audit line (no audit line at all: the
        # hook-rejection path returns before any `emit_audit` call).
        logs = "".join(p.read_text() for p in config.audit_dir.glob("*.jsonl"))
        assert '"tool":"refresh_apply"' not in logs
