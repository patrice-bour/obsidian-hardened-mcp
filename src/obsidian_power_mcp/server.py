"""MCP server registration.

`create_server(config)` builds a `FastMCP` instance with every M1 tool
wired to its underlying implementation. The server runs over stdio.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ToolResult
from obsidian_power_mcp.tools.meta import get_vault_info as _get_vault_info_impl
from obsidian_power_mcp.tools.meta import (
    list_tools_capabilities as _list_tools_capabilities_impl,
)
from obsidian_power_mcp.tools.read import list_notes as _list_notes_impl
from obsidian_power_mcp.tools.read import read_note as _read_note_impl


def create_server(config: AppConfig) -> FastMCP:
    """Build a FastMCP server bound to the given configuration."""
    app = FastMCP(name="obsidian-power-mcp")

    @app.tool(description="Read a note's full text content from the vault.")
    def read_note(path: str) -> ToolResult:
        return _read_note_impl(config, path)

    @app.tool(description="List markdown notes in the vault, optionally filtered by folder.")
    def list_notes(folder: str | None = None, limit: int = 200) -> ToolResult:
        return _list_notes_impl(config, folder=folder, limit=limit)

    @app.tool(description="Return vault metadata (root, note count, limits, server identity).")
    def get_vault_info() -> ToolResult:
        return _get_vault_info_impl(config)

    @app.tool(description="Return the manifest of tools available on this server.")
    def list_tools_capabilities() -> ToolResult:
        return _list_tools_capabilities_impl(config)

    return app
