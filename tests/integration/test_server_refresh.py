# SPDX-License-Identifier: Apache-2.0
"""list_stale_notes is registered on the FastMCP server."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.server import create_server
from obsidian_hardened_mcp.validation.hooks import HookRegistry


@pytest.mark.asyncio
async def test_list_stale_notes_registered(tmp_vault: Path, tmp_path: Path) -> None:
    server = create_server(
        AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit"),
        hooks=HookRegistry([]),
    )
    registered = {t.name for t in await server.list_tools()}
    assert "list_stale_notes" in registered


@pytest.mark.asyncio
async def test_list_stale_notes_listed_in_capabilities(
    tmp_vault: Path, tmp_path: Path
) -> None:
    server = create_server(
        AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit"),
        hooks=HookRegistry([]),
    )
    raw = await server.call_tool("list_tools_capabilities", {})
    assert "list_stale_notes" in str(raw)


@pytest.mark.asyncio
async def test_list_stale_notes_tool_is_callable_through_mcp(
    tmp_vault: Path, tmp_path: Path
) -> None:
    (tmp_vault / "01_Notes" / "contracted.md").write_text(
        "---\nrefresh_every: 30d\nrefresh_last: 2020-01-01\n---\nBody\n"
    )
    server = create_server(
        AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit"),
        hooks=HookRegistry([]),
    )
    raw = await server.call_tool("list_stale_notes", {})
    text = str(raw)
    assert "01_Notes/contracted.md" in text
