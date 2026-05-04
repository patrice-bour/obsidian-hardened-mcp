"""Write-side safety tests for frontmatter atomic operations.

Covers code-review findings C4 (write-time type whitelist), C5 (deep-merge
type-mismatch behaviour) and C6 (dry-run must not mutate in-memory state).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.domain.results import ErrorCode
from obsidian_full_mcp.frontmatter import parse_note
from obsidian_full_mcp.security.audit_logger import AuditLogger
from obsidian_full_mcp.tools.frontmatter import (
    merge_frontmatter,
    set_frontmatter_field,
)


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


@pytest.fixture
def fm_note(tmp_vault: Path) -> Path:
    target = tmp_vault / "01_Notes" / "fm.md"
    target.write_text("---\ntitle: Hello\n---\nBody\n")
    return target


# ---------------------------------------------------------------------------
# C4 — write-time type whitelist
# ---------------------------------------------------------------------------


class TestWriteTypeWhitelist:
    @pytest.mark.parametrize(
        "value",
        [
            b"raw bytes",
            Path("/tmp/x"),
            {1, 2, 3},
            frozenset([1, 2]),
            (1, 2, 3),
            object(),
            date(2026, 5, 4),  # use the ISO string form, not a date object
        ],
    )
    def test_unsafe_value_types_are_rejected(
        self,
        value: object,
        config: AppConfig,
        audit: AuditLogger,
        fm_note: Path,
    ) -> None:
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "field", value
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSAFE_YAML

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            False,
            0,
            42,
            -7,
            3.14,
            "",
            "hello",
            "Café à Paris",  # UTF-8 OK
            [1, 2, "three"],
            {"key": "value"},
            {"nested": {"deep": [1, {"deeper": "ok"}]}},
        ],
    )
    def test_safe_value_types_are_accepted(
        self,
        value: object,
        config: AppConfig,
        audit: AuditLogger,
        fm_note: Path,
    ) -> None:
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "field", value
        )
        assert result.ok, result.error

    def test_dict_with_non_string_key_is_rejected(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "field", {1: "x"}
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSAFE_YAML

    def test_string_too_long_is_rejected(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        huge = "x" * (100 * 1024)  # 100 KiB > 64 KiB cap
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "blob", huge
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSAFE_YAML

    def test_excessive_nesting_is_rejected(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        # Build dict nested 20 deep.
        nested: object = "leaf"
        for _ in range(20):
            nested = {"x": nested}
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "tree", nested
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSAFE_YAML

    def test_merge_frontmatter_rejects_unsafe_nested_value(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        # bytes hidden inside the patch dict must be rejected before any write.
        result = merge_frontmatter(
            config, audit, "01_Notes/fm.md", {"meta": {"raw": b"bytes"}}
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSAFE_YAML


# ---------------------------------------------------------------------------
# C5 — deep merge behaviour on type mismatches
# ---------------------------------------------------------------------------


class TestDeepMergeTypeMismatch:
    def test_dict_patch_replaces_list_target(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "n.md"
        target.write_text("---\nmeta:\n  - one\n  - two\n---\n")
        result = merge_frontmatter(
            config, audit, "01_Notes/n.md", {"meta": {"a": 1}}, mode="deep"
        )
        assert result.ok
        new = parse_note(target.read_text())
        assert new.frontmatter is not None
        assert dict(new.frontmatter["meta"]) == {"a": 1}

    def test_list_patch_replaces_dict_target(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "n.md"
        target.write_text("---\nmeta:\n  a: 1\n---\n")
        result = merge_frontmatter(
            config, audit, "01_Notes/n.md", {"meta": ["x", "y"]}, mode="deep"
        )
        assert result.ok
        new = parse_note(target.read_text())
        assert new.frontmatter is not None
        assert list(new.frontmatter["meta"]) == ["x", "y"]

    def test_scalar_patch_replaces_dict_target(
        self, config: AppConfig, audit: AuditLogger, tmp_vault: Path
    ) -> None:
        target = tmp_vault / "01_Notes" / "n.md"
        target.write_text("---\nmeta:\n  a: 1\n---\n")
        result = merge_frontmatter(
            config, audit, "01_Notes/n.md", {"meta": 42}, mode="deep"
        )
        assert result.ok
        new = parse_note(target.read_text())
        assert new.frontmatter is not None
        assert new.frontmatter["meta"] == 42

    def test_none_patch_writes_null(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = merge_frontmatter(
            config, audit, "01_Notes/fm.md", {"title": None}, mode="deep"
        )
        assert result.ok
        new = parse_note(fm_note.read_text())
        assert new.frontmatter is not None
        assert new.frontmatter["title"] is None


# ---------------------------------------------------------------------------
# C6 — dry-run must not mutate disk OR in-memory state
# ---------------------------------------------------------------------------


class TestDryRunImmutability:
    def test_dry_run_set_does_not_change_file_bytes(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        before = fm_note.read_bytes()
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "title", "Other", dry_run=True
        )
        assert result.ok
        assert result.dry_run is True
        assert fm_note.read_bytes() == before

    def test_dry_run_merge_does_not_change_file_bytes(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        before = fm_note.read_bytes()
        result = merge_frontmatter(
            config,
            audit,
            "01_Notes/fm.md",
            {"new": "field"},
            mode="shallow",
            dry_run=True,
        )
        assert result.ok
        assert fm_note.read_bytes() == before

    def test_dry_run_returns_preview_content(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "title", "Preview", dry_run=True
        )
        assert result.data is not None
        assert "title: Preview" in result.data["new_content"]


# ---------------------------------------------------------------------------
# Request ID propagation across the audit log
# ---------------------------------------------------------------------------


def _last_audit_record(audit_dir: Path) -> dict[str, object]:
    files = sorted(audit_dir.glob("*.jsonl"))
    assert files, "no audit log file"
    line = files[-1].read_text().splitlines()[-1]
    return json.loads(line)


class TestRequestIdPropagation:
    def test_request_id_in_data_matches_audit_log(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        result = set_frontmatter_field(
            config, audit, "01_Notes/fm.md", "title", "X"
        )
        assert result.ok
        assert result.data is not None
        record = _last_audit_record(config.audit_dir)
        assert record["request_id"] == result.data["request_id"]

    def test_two_calls_get_distinct_request_ids(
        self, config: AppConfig, audit: AuditLogger, fm_note: Path
    ) -> None:
        a = set_frontmatter_field(config, audit, "01_Notes/fm.md", "title", "A")
        b = set_frontmatter_field(config, audit, "01_Notes/fm.md", "title", "B")
        assert a.data is not None and b.data is not None
        assert a.data["request_id"] != b.data["request_id"]
