"""Integration: validation hooks wired into write tools.

Proves the hook registry actually short-circuits writes — both for content
tools (`create_note`, `update_note`, `append_to_note`, `patch_note`) and
for frontmatter atomic ops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.frontmatter import (
    delete_frontmatter_field,
    merge_frontmatter,
    set_frontmatter_field,
)
from obsidian_hardened_mcp.tools.write import (
    append_to_note,
    create_note,
    patch_note,
    update_note,
)
from obsidian_hardened_mcp.validation.builtin_hooks import (
    IsoDateHook,
    JsonSchemaHook,
    ReservedTagsHook,
)
from obsidian_hardened_mcp.validation.hooks import HookRegistry


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


@pytest.fixture
def fm_note(tmp_vault: Path) -> Path:
    target = tmp_vault / "01_Notes" / "fm.md"
    target.write_text("---\ntype: journal\ndate: 2026-05-04\n---\nBody\n")
    return target


# ---------------------------------------------------------------------------
# create_note + iso_date hook
# ---------------------------------------------------------------------------


class TestCreateNoteWithIsoDateHook:
    def test_iso_date_hook_blocks_bad_date(
        self,
        config: AppConfig,
        audit: AuditLogger,
        tmp_vault: Path,
    ) -> None:
        hooks = HookRegistry([IsoDateHook()])
        result = create_note(
            config,
            audit,
            "01_Notes/new.md",
            "---\ndate: tomorrow\n---\nBody\n",
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
        assert "iso_date" in result.error.message
        # File must NOT have been created.
        assert not (tmp_vault / "01_Notes" / "new.md").exists()

    def test_iso_date_hook_allows_good_date(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        hooks = HookRegistry([IsoDateHook()])
        result = create_note(
            config,
            audit,
            "01_Notes/new.md",
            "---\ndate: 2026-05-04\n---\nBody\n",
            hooks=hooks,
        )
        assert result.ok
        assert (tmp_vault / "01_Notes" / "new.md").exists()


# ---------------------------------------------------------------------------
# Hooks block dry-run too — preview must surface the same yes/no
# ---------------------------------------------------------------------------


class TestDryRunStillValidated:
    def test_dry_run_failed_validation_returns_failure(
        self,
        config: AppConfig,
        audit: AuditLogger,
        tmp_vault: Path,
    ) -> None:
        hooks = HookRegistry([IsoDateHook()])
        result = create_note(
            config,
            audit,
            "01_Notes/preview.md",
            "---\ndate: bad\n---\n",
            hooks=hooks,
            dry_run=True,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED


# ---------------------------------------------------------------------------
# update_note / append_to_note / patch_note
# ---------------------------------------------------------------------------


class TestUpdateNoteWithHooks:
    def test_update_with_invalid_frontmatter_is_blocked(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        hooks = HookRegistry([IsoDateHook()])
        original = fm_note.read_text()
        result = update_note(
            config,
            audit,
            "01_Notes/fm.md",
            "---\ndate: yesterday\n---\nUpdated\n",
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
        assert fm_note.read_text() == original  # untouched

    def test_append_with_pure_body_change_is_accepted(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        hooks = HookRegistry([IsoDateHook()])
        result = append_to_note(
            config, audit, "01_Notes/fm.md", "more body\n", hooks=hooks
        )
        assert result.ok

    def test_patch_that_corrupts_frontmatter_is_blocked(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        hooks = HookRegistry([IsoDateHook()])
        original = fm_note.read_text()
        # The patch swaps the valid date for an invalid one.
        result = patch_note(
            config,
            audit,
            "01_Notes/fm.md",
            "2026-05-04",
            "tomorrow",
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
        assert fm_note.read_text() == original


# ---------------------------------------------------------------------------
# Reserved tags hook
# ---------------------------------------------------------------------------


class TestReservedTagsHook:
    def test_blocks_forbidden_tag(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        hooks = HookRegistry([ReservedTagsHook(forbidden=["migration-cc"])])
        result = set_frontmatter_field(
            config,
            audit,
            "01_Notes/fm.md",
            "tags",
            ["foo", "migration-cc"],
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
        assert "migration-cc" in result.error.message

    def test_blocks_forbidden_field(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        hooks = HookRegistry(
            [ReservedTagsHook(forbidden_fields=["source-vault"])]
        )
        result = set_frontmatter_field(
            config,
            audit,
            "01_Notes/fm.md",
            "source-vault",
            "PBR",
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED


# ---------------------------------------------------------------------------
# JSON Schema hook
# ---------------------------------------------------------------------------


_OFFRE_SCHEMA = {
    "type": "object",
    "required": ["type", "recruteur"],
    "properties": {
        "type": {"const": "offre-emploi"},
        "recruteur": {"type": "string"},
    },
    "additionalProperties": True,
}


class TestJsonSchemaHook:
    def test_create_note_with_valid_offre_is_accepted(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        hooks = HookRegistry(
            [JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})]
        )
        result = create_note(
            config,
            audit,
            "01_Notes/offre.md",
            "---\ntype: offre-emploi\nrecruteur: Acme\n---\nBody\n",
            hooks=hooks,
        )
        assert result.ok
        assert (tmp_vault / "01_Notes" / "offre.md").exists()

    def test_missing_required_field_is_rejected(
        self,
        config: AppConfig,
        audit: AuditLogger,
        tmp_vault: Path,
    ) -> None:
        hooks = HookRegistry(
            [JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})]
        )
        result = create_note(
            config,
            audit,
            "01_Notes/bad.md",
            "---\ntype: offre-emploi\n---\nBody\n",
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
        assert "recruteur" in result.error.message


# ---------------------------------------------------------------------------
# Hook ordering — first reject wins
# ---------------------------------------------------------------------------


class TestHookOrder:
    def test_first_failing_hook_short_circuits(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        # Two hooks would fail; only the first one's reason should surface.
        hooks = HookRegistry(
            [
                ReservedTagsHook(forbidden=["bad-tag"]),
                IsoDateHook(),
            ]
        )
        result = set_frontmatter_field(
            config,
            audit,
            "01_Notes/fm.md",
            "tags",
            ["bad-tag"],  # would fail reserved_tags
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
        assert "reserved_tags" in result.error.message
        assert "iso_date" not in result.error.message


# ---------------------------------------------------------------------------
# Empty registry & no-hooks default
# ---------------------------------------------------------------------------


class TestEmptyAndDefault:
    def test_empty_registry_does_not_block(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        hooks = HookRegistry([])
        result = create_note(
            config,
            audit,
            "01_Notes/free.md",
            "---\ndate: nonsense\n---\n",
            hooks=hooks,
        )
        assert result.ok

    def test_no_hooks_argument_skips_validation(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        # Default `hooks=None` → no validation, backward-compatible.
        result = create_note(
            config, audit, "01_Notes/free2.md", "---\ndate: x\n---\n"
        )
        assert result.ok


# ---------------------------------------------------------------------------
# delete_frontmatter_field + merge_frontmatter under hooks
# ---------------------------------------------------------------------------


class TestFrontmatterAtomicWithHooks:
    def test_delete_field_can_be_blocked_when_resulting_state_invalid(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        # `IsoDateHook` does NOT reject when the field is absent — it only
        # validates if `date:` IS present. Deleting `date:` therefore
        # ALLOWS the operation (empty fm has no `date`). Sanity check.
        hooks = HookRegistry([IsoDateHook()])
        result = delete_frontmatter_field(
            config, audit, "01_Notes/fm.md", "date", hooks=hooks
        )
        assert result.ok

    def test_merge_can_introduce_invalid_date_and_be_blocked(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        original = fm_note.read_text()
        hooks = HookRegistry([IsoDateHook()])
        result = merge_frontmatter(
            config,
            audit,
            "01_Notes/fm.md",
            {"date": "tomorrow"},
            mode="shallow",
            hooks=hooks,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
        assert fm_note.read_text() == original
