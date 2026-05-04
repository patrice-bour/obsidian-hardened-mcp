"""Smoke integration test for the FastMCP server wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.server import create_server


@pytest.fixture
def config(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault)


def test_create_server_returns_fastmcp_instance(config: AppConfig) -> None:
    server = create_server(config)
    assert isinstance(server, FastMCP)
    assert server.name == "obsidian-power-mcp"


@pytest.mark.asyncio
async def test_registered_tools_match_capabilities_manifest(
    config: AppConfig,
) -> None:
    """The MCP-exposed tool names MUST match the manifest from
    `list_tools_capabilities` so clients can rely on it."""
    server = create_server(config)
    registered = {t.name for t in await server.list_tools()}
    expected = {
        "read_note",
        "list_notes",
        "get_frontmatter",
        "get_vault_info",
        "list_tools_capabilities",
    }
    assert expected <= registered


@pytest.mark.asyncio
async def test_read_note_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    """End-to-end: the MCP `read_note` tool returns the note content."""
    server = create_server(config)
    raw = await server.call_tool("read_note", {"path": "01_Notes/sample.md"})
    # call_tool returns a (content, structured) tuple in the MCP SDK; we
    # just need to confirm the call succeeded and yielded the expected text.
    assert "# Sample" in str(raw)


@pytest.mark.asyncio
async def test_list_notes_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("list_notes", {"folder": None, "limit": 200})
    assert "01_Notes/sample.md" in str(raw)


@pytest.mark.asyncio
async def test_get_vault_info_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("get_vault_info", {})
    assert "obsidian-power-mcp" in str(raw)


@pytest.mark.asyncio
async def test_list_tools_capabilities_tool_is_callable_through_mcp(
    config: AppConfig,
) -> None:
    server = create_server(config)
    raw = await server.call_tool("list_tools_capabilities", {})
    assert "read_note" in str(raw)
    assert "get_frontmatter" in str(raw)


@pytest.mark.asyncio
async def test_get_frontmatter_tool_is_callable_through_mcp(
    config: AppConfig, tmp_vault: Path
) -> None:
    (tmp_vault / "01_Notes" / "fm.md").write_text(
        "---\ntitle: MCP\n---\nBody\n"
    )
    server = create_server(config)
    raw = await server.call_tool("get_frontmatter", {"path": "01_Notes/fm.md"})
    assert "MCP" in str(raw)
