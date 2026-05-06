"""Tests for the validation config loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import TrashPolicy
from obsidian_hardened_mcp.validation.builtin_hooks import (
    IsoDateHook,
    JsonSchemaHook,
    ReservedTagsHook,
)
from obsidian_hardened_mcp.validation.config_loader import (
    ConfigError,
    load_trash_policy,
    load_validation_config,
)


class TestNoConfigFile:
    def test_missing_file_yields_empty_registry(self, tmp_vault: Path) -> None:
        # The bootstrap config from the fixture is `schemas: {}` — no hooks.
        registry = load_validation_config(tmp_vault)
        assert registry.hooks == ()


class TestSimpleHooks:
    def test_iso_date_hook_with_no_args(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n  - iso_date\n"
        )
        registry = load_validation_config(tmp_vault)
        assert len(registry.hooks) == 1
        assert isinstance(registry.hooks[0], IsoDateHook)

    def test_reserved_tags_hook_with_args(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - reserved_tags:\n"
            "      forbidden:\n"
            "        - migration-cc\n"
            "      forbidden_fields:\n"
            "        - source-vault\n"
        )
        registry = load_validation_config(tmp_vault)
        assert len(registry.hooks) == 1
        hook = registry.hooks[0]
        assert isinstance(hook, ReservedTagsHook)

    def test_iso_date_with_custom_fields(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - iso_date:\n"
            "      fields: [date, due-date]\n"
        )
        registry = load_validation_config(tmp_vault)
        assert isinstance(registry.hooks[0], IsoDateHook)

    def test_multiple_hooks_in_declared_order(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - iso_date\n"
            "  - reserved_tags:\n"
            "      forbidden: [foo]\n"
        )
        registry = load_validation_config(tmp_vault)
        assert [h.name for h in registry.hooks] == ["iso_date", "reserved_tags"]


class TestJsonSchemaHook:
    def _write_schema(self, vault: Path, name: str, schema: dict) -> Path:
        target = vault / "_schemas" / f"{name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(schema))
        return target

    def test_loads_schemas_from_relative_paths(self, tmp_vault: Path) -> None:
        self._write_schema(
            tmp_vault,
            "offre-emploi",
            {
                "type": "object",
                "required": ["recruteur"],
                "properties": {"recruteur": {"type": "string"}},
            },
        )
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - json_schema:\n"
            "      schemas:\n"
            "        offre-emploi: _schemas/offre-emploi.json\n"
        )
        registry = load_validation_config(tmp_vault)
        assert isinstance(registry.hooks[0], JsonSchemaHook)

    def test_missing_schema_file_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - json_schema:\n"
            "      schemas:\n"
            "        ghost: _schemas/ghost.json\n"
        )
        with pytest.raises(ConfigError) as exc_info:
            load_validation_config(tmp_vault)
        assert "ghost.json" in str(exc_info.value)

    def test_schema_path_must_stay_inside_vault(self, tmp_vault: Path) -> None:
        # Path traversal in the schemas map MUST be rejected.
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - json_schema:\n"
            "      schemas:\n"
            "        evil: ../escape.json\n"
        )
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)

    def test_invalid_schema_is_rejected_at_load(self, tmp_vault: Path) -> None:
        # Schema that is not a valid Draft 2020-12 schema.
        (tmp_vault / "_schemas").mkdir()
        (tmp_vault / "_schemas" / "bad.json").write_text(
            json.dumps({"type": ["not", "a", "valid", "type"]})
        )
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - json_schema:\n"
            "      schemas:\n"
            "        bad: _schemas/bad.json\n"
        )
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)


class TestErrorHandling:
    def test_unknown_hook_name_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n  - mystery_hook\n"
        )
        with pytest.raises(ConfigError) as exc_info:
            load_validation_config(tmp_vault)
        assert "mystery_hook" in str(exc_info.value)

    def test_invalid_yaml_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n  - [unclosed\n"
        )
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)

    def test_unknown_hook_arg_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n"
            "  - iso_date:\n"
            "      not_a_real_arg: 1\n"
        )
        with pytest.raises(ConfigError) as exc_info:
            load_validation_config(tmp_vault)
        assert "not_a_real_arg" in str(exc_info.value)

    def test_top_level_must_be_mapping(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text("- not\n- a\n- mapping\n")
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)


class TestNoHooksSection:
    def test_config_without_hooks_yields_empty_registry(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "schemas: {}\nlimits:\n  max_file_size_mb: 5\n"
        )
        registry = load_validation_config(tmp_vault)
        assert registry.hooks == ()


class TestLoadTrashPolicy:
    def test_no_config_file_yields_default_policy(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").unlink()
        policy = load_trash_policy(tmp_vault)
        assert policy == TrashPolicy()

    def test_no_trash_block_yields_default_policy(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "hooks:\n  - iso_date\n"
        )
        policy = load_trash_policy(tmp_vault)
        assert policy == TrashPolicy()

    def test_full_trash_block_parsed(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "trash:\n"
            "  retention_days: 60\n"
            "  keep_at_least_per_path: 3\n"
            "  keep_at_least_global: 10\n"
            "  max_total_mb: 100\n"
        )
        policy = load_trash_policy(tmp_vault)
        assert policy.retention_days == 60
        assert policy.keep_at_least_per_path == 3
        assert policy.keep_at_least_global == 10
        assert policy.max_total_mb == 100

    def test_partial_trash_block_uses_defaults_for_missing_keys(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "trash:\n  retention_days: 7\n"
        )
        policy = load_trash_policy(tmp_vault)
        assert policy.retention_days == 7
        # Other fields fall back to TrashPolicy() defaults
        assert policy.keep_at_least_per_path == 1
        assert policy.keep_at_least_global == 5
        assert policy.max_total_mb is None

    def test_null_retention_disables_time_pruning(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "trash:\n  retention_days: null\n"
        )
        policy = load_trash_policy(tmp_vault)
        assert policy.retention_days is None

    def test_unknown_key_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "trash:\n  retention_days: 30\n  bogus_key: 42\n"
        )
        with pytest.raises(ConfigError, match="bogus_key"):
            load_trash_policy(tmp_vault)

    def test_negative_retention_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "trash:\n  retention_days: -5\n"
        )
        with pytest.raises(ConfigError):
            load_trash_policy(tmp_vault)

    def test_non_mapping_trash_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
            "trash:\n  - 1\n  - 2\n"
        )
        with pytest.raises(ConfigError, match="trash"):
            load_trash_policy(tmp_vault)
