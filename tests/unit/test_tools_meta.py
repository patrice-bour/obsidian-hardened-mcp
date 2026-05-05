"""Unit tests for tools.meta."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.tools.meta import get_vault_info, list_tools_capabilities


@pytest.fixture
def config(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault)


class TestGetVaultInfo:
    def test_reports_basic_metadata(self, config: AppConfig, tmp_vault: Path) -> None:
        result = get_vault_info(config)
        assert result.ok
        assert result.data is not None
        assert result.data["vault_root"] == str(tmp_vault.resolve())
        assert result.data["note_count"] == 3  # _VAULT.md + journal + sample
        assert result.data["max_file_size_mb"] == config.max_file_size_mb
        assert result.data["rest_available"] is False  # no detection in M1
        # Server identity
        assert "server_name" in result.data
        assert "server_version" in result.data


class TestListToolsCapabilities:
    def test_returns_tool_manifest(self, config: AppConfig) -> None:
        result = list_tools_capabilities(config)
        assert result.ok
        assert result.data is not None
        tools = result.data["tools"]
        names = {t["name"] for t in tools}
        # M1 tools
        assert {"read_note", "list_notes", "get_vault_info", "list_tools_capabilities"} <= names
        # Each tool has a kind
        for tool in tools:
            assert tool["kind"] in {"read", "write", "destructive", "meta"}
