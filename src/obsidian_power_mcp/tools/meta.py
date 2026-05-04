"""Meta tools — `get_vault_info`, `list_tools_capabilities`."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ToolResult
from obsidian_power_mcp.fs.listing import iter_markdown
from obsidian_power_mcp.tools._base import tool_call

SERVER_NAME = "obsidian-power-mcp"


def _server_version() -> str:
    try:
        return version(SERVER_NAME)
    except PackageNotFoundError:  # pragma: no cover - dev install fallback
        return "0.0.0+local"


@tool_call
def get_vault_info(config: AppConfig) -> ToolResult:
    """Return vault metadata and server runtime info."""
    note_count = sum(1 for _ in iter_markdown(config.vault_root))
    return ToolResult.success(
        data={
            "vault_root": str(config.vault_root),
            "note_count": note_count,
            "max_file_size_mb": config.max_file_size_mb,
            "max_batch": config.max_batch,
            "rest_available": False,  # populated by REST detector in M7
            "server_name": SERVER_NAME,
            "server_version": _server_version(),
        }
    )


@tool_call
def list_tools_capabilities(config: AppConfig) -> ToolResult:
    """Return a manifest of every tool the server exposes.

    Useful for clients that want to validate which features are available.
    Each entry includes a `kind` (read | write | destructive | meta) the
    client can use to apply UI-level confirmation policies.
    """
    tools: list[dict[str, Any]] = [
        {"name": "read_note", "kind": "read", "description": "Read a note's text content."},
        {"name": "list_notes", "kind": "read", "description": "List markdown notes."},
        {
            "name": "get_frontmatter",
            "kind": "read",
            "description": "Parse a note's YAML frontmatter (round-trip aware).",
        },
        {"name": "get_vault_info", "kind": "meta", "description": "Vault metadata."},
        {
            "name": "list_tools_capabilities",
            "kind": "meta",
            "description": "Tools available on this server.",
        },
    ]
    return ToolResult.success(data={"tools": tools})
