"""Tests for built-in validation hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obsidian_power_mcp.domain.vault_path import VaultPath
from obsidian_power_mcp.validation.builtin_hooks import (
    IsoDateHook,
    JsonSchemaHook,
    ReservedTagsHook,
)
from obsidian_power_mcp.validation.hooks import HookContext


def _ctx(
    tmp_vault: Path, frontmatter: dict[str, Any] | None, *, body: str = "Body"
) -> HookContext:
    return HookContext(
        path=VaultPath.from_user("01_Notes/x.md", tmp_vault),
        new_frontmatter=frontmatter,
        new_body=body,
        operation="set_frontmatter_field",
    )


# ---------------------------------------------------------------------------
# IsoDateHook
# ---------------------------------------------------------------------------


class TestIsoDateHook:
    def test_no_frontmatter_is_accepted(self, tmp_vault: Path) -> None:
        result = IsoDateHook().validate(_ctx(tmp_vault, None))
        assert result.decision == "accept"

    def test_no_date_field_is_accepted(self, tmp_vault: Path) -> None:
        result = IsoDateHook().validate(_ctx(tmp_vault, {"title": "x"}))
        assert result.decision == "accept"

    def test_iso_date_string_is_accepted(self, tmp_vault: Path) -> None:
        result = IsoDateHook().validate(
            _ctx(tmp_vault, {"date": "2026-05-04"})
        )
        assert result.decision == "accept"

    @pytest.mark.parametrize(
        "bad_value",
        ["2026/05/04", "04-05-2026", "May 4, 2026", "2026-5-4", "today", "", "2026-13-01"],
    )
    def test_non_iso_date_is_rejected(self, tmp_vault: Path, bad_value: str) -> None:
        result = IsoDateHook().validate(_ctx(tmp_vault, {"date": bad_value}))
        assert result.decision == "reject"
        assert "ISO" in (result.reason or "")

    def test_iso_datetime_is_accepted(self, tmp_vault: Path) -> None:
        result = IsoDateHook().validate(
            _ctx(tmp_vault, {"date": "2026-05-04T10:30:00Z"})
        )
        assert result.decision == "accept"

    def test_non_string_date_is_rejected(self, tmp_vault: Path) -> None:
        result = IsoDateHook().validate(_ctx(tmp_vault, {"date": 20260504}))
        assert result.decision == "reject"

    def test_custom_field_name(self, tmp_vault: Path) -> None:
        # Allow validating other date-like fields by configuration.
        hook = IsoDateHook(fields=("date", "due-date"))
        ok = hook.validate(_ctx(tmp_vault, {"due-date": "2026-05-04"}))
        bad = hook.validate(_ctx(tmp_vault, {"due-date": "tomorrow"}))
        assert ok.decision == "accept"
        assert bad.decision == "reject"


# ---------------------------------------------------------------------------
# ReservedTagsHook
# ---------------------------------------------------------------------------


class TestReservedTagsHook:
    def test_no_tags_is_accepted(self, tmp_vault: Path) -> None:
        hook = ReservedTagsHook(forbidden=["migration-cc"])
        assert hook.validate(_ctx(tmp_vault, {"title": "x"})).decision == "accept"

    def test_clean_tags_are_accepted(self, tmp_vault: Path) -> None:
        hook = ReservedTagsHook(forbidden=["migration-cc"])
        assert (
            hook.validate(_ctx(tmp_vault, {"tags": ["foo", "bar"]})).decision
            == "accept"
        )

    def test_forbidden_tag_is_rejected(self, tmp_vault: Path) -> None:
        hook = ReservedTagsHook(forbidden=["migration-cc"])
        result = hook.validate(
            _ctx(tmp_vault, {"tags": ["foo", "migration-cc"]})
        )
        assert result.decision == "reject"
        assert "migration-cc" in (result.reason or "")

    def test_forbidden_field_presence_is_rejected(self, tmp_vault: Path) -> None:
        hook = ReservedTagsHook(forbidden_fields=["source-vault"])
        result = hook.validate(
            _ctx(tmp_vault, {"source-vault": "PBR", "title": "x"})
        )
        assert result.decision == "reject"
        assert "source-vault" in (result.reason or "")

    def test_hierarchical_forbidden_tag(self, tmp_vault: Path) -> None:
        hook = ReservedTagsHook(forbidden=["migration/pbr"])
        bad = hook.validate(_ctx(tmp_vault, {"tags": ["migration/pbr"]}))
        ok = hook.validate(_ctx(tmp_vault, {"tags": ["migration"]}))
        assert bad.decision == "reject"
        assert ok.decision == "accept"

    def test_no_frontmatter_is_accepted(self, tmp_vault: Path) -> None:
        hook = ReservedTagsHook(forbidden=["migration-cc"])
        assert hook.validate(_ctx(tmp_vault, None)).decision == "accept"


# ---------------------------------------------------------------------------
# JsonSchemaHook
# ---------------------------------------------------------------------------


_OFFRE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["type", "date", "recruteur"],
    "properties": {
        "type": {"const": "offre-emploi"},
        "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "recruteur": {"type": "string"},
        "poste": {"type": "string"},
    },
    "additionalProperties": True,
}


class TestJsonSchemaHook:
    def test_no_type_means_accept(self, tmp_vault: Path) -> None:
        hook = JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})
        result = hook.validate(_ctx(tmp_vault, {"title": "x"}))
        assert result.decision == "accept"

    def test_unknown_type_is_accepted(self, tmp_vault: Path) -> None:
        # No schema registered for `journal` → not the schema's job to forbid
        # unknown types; that's the role of a separate type-whitelist hook.
        hook = JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})
        result = hook.validate(_ctx(tmp_vault, {"type": "journal"}))
        assert result.decision == "accept"

    def test_valid_offre_is_accepted(self, tmp_vault: Path) -> None:
        hook = JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})
        fm = {
            "type": "offre-emploi",
            "date": "2026-05-04",
            "recruteur": "Acme",
            "poste": "Lead Developer",
        }
        result = hook.validate(_ctx(tmp_vault, fm))
        assert result.decision == "accept"

    def test_missing_required_field_is_rejected(self, tmp_vault: Path) -> None:
        hook = JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})
        fm = {"type": "offre-emploi", "date": "2026-05-04"}  # missing recruteur
        result = hook.validate(_ctx(tmp_vault, fm))
        assert result.decision == "reject"
        assert "recruteur" in (result.reason or "")

    def test_wrong_type_for_field_is_rejected(self, tmp_vault: Path) -> None:
        hook = JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})
        fm = {
            "type": "offre-emploi",
            "date": "2026-05-04",
            "recruteur": 42,  # int instead of str
        }
        result = hook.validate(_ctx(tmp_vault, fm))
        assert result.decision == "reject"

    def test_pattern_mismatch_is_rejected(self, tmp_vault: Path) -> None:
        hook = JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})
        fm = {
            "type": "offre-emploi",
            "date": "tomorrow",  # fails ISO pattern
            "recruteur": "Acme",
        }
        result = hook.validate(_ctx(tmp_vault, fm))
        assert result.decision == "reject"

    def test_no_frontmatter_is_accepted(self, tmp_vault: Path) -> None:
        hook = JsonSchemaHook(schemas={"offre-emploi": _OFFRE_SCHEMA})
        assert hook.validate(_ctx(tmp_vault, None)).decision == "accept"
