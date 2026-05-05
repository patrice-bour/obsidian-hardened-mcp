"""Regression tests for M4 code-review findings.

Each section maps to a finding from the M4 review (see
docs/v0.1-followups.md and the commit message).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode
from obsidian_hardened_mcp.domain.vault_path import VaultPath
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.frontmatter import set_frontmatter_field
from obsidian_hardened_mcp.validation.builtin_hooks import JsonSchemaHook
from obsidian_hardened_mcp.validation.config_loader import (
    ConfigError,
    load_validation_config,
)
from obsidian_hardened_mcp.validation.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
)


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


# ===========================================================================
# M3 — HookContext is mutation-isolated between hooks
# ===========================================================================


class _MutatingHook:
    """A hook that mutates `ctx.new_frontmatter` to test isolation."""

    name = "mutator"
    phase = "pre_write"

    def validate(self, ctx: HookContext) -> HookResult:
        if ctx.new_frontmatter is not None:
            ctx.new_frontmatter["injected"] = "by_first_hook"
        return HookResult.accept()


class _SnapshotHook:
    """A hook that records what it saw."""

    name = "snapshot"
    phase = "pre_write"

    def __init__(self) -> None:
        self.saw: dict[str, object] | None = None

    def validate(self, ctx: HookContext) -> HookResult:
        self.saw = (
            None
            if ctx.new_frontmatter is None
            else dict(ctx.new_frontmatter)
        )
        return HookResult.accept()


class TestHookContextIsolation:
    def test_first_hook_mutation_does_not_leak_to_next(
        self, tmp_vault: Path
    ) -> None:
        snapshot = _SnapshotHook()
        registry = HookRegistry([_MutatingHook(), snapshot])
        ctx = HookContext(
            path=VaultPath.from_user("01_Notes/sample.md", tmp_vault),
            new_frontmatter={"title": "x"},
            new_body="body",
            operation="set_frontmatter_field",
        )
        report = registry.run(ctx)
        assert report.allowed is True
        # The snapshot hook MUST NOT have seen the injection from the first
        # hook. Mutation isolation is the contract.
        assert snapshot.saw == {"title": "x"}

    def test_caller_view_of_frontmatter_is_unchanged(
        self, tmp_vault: Path
    ) -> None:
        registry = HookRegistry([_MutatingHook()])
        original_fm = {"title": "x"}
        ctx = HookContext(
            path=VaultPath.from_user("01_Notes/sample.md", tmp_vault),
            new_frontmatter=original_fm,
            new_body="body",
            operation="set_frontmatter_field",
        )
        registry.run(ctx)
        # The dict we passed in is also untouched (no caller-side surprise).
        assert original_fm == {"title": "x"}


# ===========================================================================
# M2 — `.obsidian-hardened-mcp.yaml` rejects custom YAML tags
# ===========================================================================


class TestConfigYamlSafety:
    def test_custom_tag_in_config_is_rejected(self, tmp_vault: Path) -> None:
        # `!Untrusted` would otherwise survive ruamel rt loading and travel
        # into hook construction as a `TaggedScalar` — refused at parse.
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - reserved_tags:\n"
            "      forbidden: !Untrusted\n"
            "        - migration-cc\n"
        )
        with pytest.raises(ConfigError) as exc_info:
            load_validation_config(tmp_vault)
        assert "tag" in str(exc_info.value).lower()

    def test_python_object_tag_in_config_is_rejected(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - reserved_tags:\n"
            "      forbidden: !!python/object/apply:os.system ['id']\n"
        )
        with pytest.raises(ConfigError) as exc_info:
            load_validation_config(tmp_vault)
        assert "tag" in str(exc_info.value).lower()

    def test_default_tags_in_config_are_accepted(self, tmp_vault: Path) -> None:
        # Plain str/list/int/bool/null still work.
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - reserved_tags:\n"
            "      forbidden: [migration-cc]\n"
            "      forbidden_fields: [source-vault]\n"
        )
        # Should load without error.
        registry = load_validation_config(tmp_vault)
        assert len(registry.hooks) == 1


# ===========================================================================
# M1 — JsonSchemaHook refuses cyclic $ref schemas at construction
# ===========================================================================


class TestJsonSchemaCyclicRefGuard:
    def test_mutually_recursive_refs_rejected_at_construction(self) -> None:
        from obsidian_hardened_mcp.validation.builtin_hooks import CyclicRefError

        cyclic = {
            "$ref": "#/$defs/A",
            "$defs": {
                "A": {"$ref": "#/$defs/B"},
                "B": {"$ref": "#/$defs/A"},
            },
        }
        with pytest.raises(CyclicRefError) as exc_info:
            JsonSchemaHook(schemas={"loop": cyclic})
        assert "loop" in str(exc_info.value)
        assert "cyclic" in str(exc_info.value).lower()

    def test_self_ref_rejected_at_construction(self) -> None:
        from obsidian_hardened_mcp.validation.builtin_hooks import CyclicRefError

        self_ref = {"$ref": "#"}
        with pytest.raises(CyclicRefError):
            JsonSchemaHook(schemas={"self": self_ref})

    def test_cyclic_schema_in_config_blocks_boot(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / "_schemas").mkdir()
        (tmp_vault / "_schemas" / "loop.json").write_text(
            '{"$ref": "#/$defs/A", "$defs": {'
            '"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}}}'
        )
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - json_schema:\n"
            "      schemas:\n"
            "        loop: _schemas/loop.json\n"
        )
        # The server must refuse to boot rather than hand the user a config
        # that locks every write with a RecursionError on first call.
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)

    def test_runtime_recursionerror_is_surfaced_as_rejection(
        self,
        config: AppConfig,
        audit: AuditLogger,
        tmp_vault: Path,
    ) -> None:
        """If a schema slips through construction (defensive only) and a
        runtime RecursionError happens, it must be a rejection — never a
        crash that escapes the hook."""

        class _RecursiveBoom:
            name = "recursive_boom"
            phase = "pre_write"

            def validate(self, ctx: HookContext) -> HookResult:
                raise RecursionError("simulated runaway")

        hooks = HookRegistry([_RecursiveBoom()])
        target = tmp_vault / "01_Notes" / "x.md"
        target.write_text("---\ntitle: Hi\n---\nBody\n")
        result = set_frontmatter_field(
            config, audit, "01_Notes/x.md", "title", "Updated", hooks=hooks
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.VALIDATION_FAILED
