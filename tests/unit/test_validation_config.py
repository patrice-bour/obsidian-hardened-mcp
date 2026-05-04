"""Tests for the validation config loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsidian_full_mcp.validation.builtin_hooks import (
    IsoDateHook,
    JsonSchemaHook,
    ReservedTagsHook,
)
from obsidian_full_mcp.validation.config_loader import (
    ConfigError,
    load_validation_config,
)


class TestNoConfigFile:
    def test_missing_file_yields_empty_registry(self, tmp_vault: Path) -> None:
        # The bootstrap config from the fixture is `schemas: {}` — no hooks.
        registry = load_validation_config(tmp_vault)
        assert registry.hooks == ()


class TestSimpleHooks:
    def test_iso_date_hook_with_no_args(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "hooks:\n  - iso_date\n"
        )
        registry = load_validation_config(tmp_vault)
        assert len(registry.hooks) == 1
        assert isinstance(registry.hooks[0], IsoDateHook)

    def test_reserved_tags_hook_with_args(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
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
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "hooks:\n"
            "  - iso_date:\n"
            "      fields: [date, due-date]\n"
        )
        registry = load_validation_config(tmp_vault)
        assert isinstance(registry.hooks[0], IsoDateHook)

    def test_multiple_hooks_in_declared_order(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
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
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "hooks:\n"
            "  - json_schema:\n"
            "      schemas:\n"
            "        offre-emploi: _schemas/offre-emploi.json\n"
        )
        registry = load_validation_config(tmp_vault)
        assert isinstance(registry.hooks[0], JsonSchemaHook)

    def test_missing_schema_file_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
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
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
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
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "hooks:\n"
            "  - json_schema:\n"
            "      schemas:\n"
            "        bad: _schemas/bad.json\n"
        )
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)


class TestErrorHandling:
    def test_unknown_hook_name_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "hooks:\n  - mystery_hook\n"
        )
        with pytest.raises(ConfigError) as exc_info:
            load_validation_config(tmp_vault)
        assert "mystery_hook" in str(exc_info.value)

    def test_invalid_yaml_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "hooks:\n  - [unclosed\n"
        )
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)

    def test_unknown_hook_arg_is_rejected(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "hooks:\n"
            "  - iso_date:\n"
            "      not_a_real_arg: 1\n"
        )
        with pytest.raises(ConfigError) as exc_info:
            load_validation_config(tmp_vault)
        assert "not_a_real_arg" in str(exc_info.value)

    def test_top_level_must_be_mapping(self, tmp_vault: Path) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text("- not\n- a\n- mapping\n")
        with pytest.raises(ConfigError):
            load_validation_config(tmp_vault)


class TestNoHooksSection:
    def test_config_without_hooks_yields_empty_registry(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / ".obsidian-full-mcp.yaml").write_text(
            "schemas: {}\nlimits:\n  max_file_size_mb: 5\n"
        )
        registry = load_validation_config(tmp_vault)
        assert registry.hooks == ()
