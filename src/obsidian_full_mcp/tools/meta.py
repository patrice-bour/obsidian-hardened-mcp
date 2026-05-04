"""Meta tools — `get_vault_info`, `list_tools_capabilities`."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.domain.results import ToolResult
from obsidian_full_mcp.fs.listing import iter_markdown
from obsidian_full_mcp.tools._base import tool_call

SERVER_NAME = "obsidian-full-mcp"


def _server_version() -> str:
    try:
        return version(SERVER_NAME)
    except PackageNotFoundError:  # pragma: no cover - dev install fallback
        return "0.0.0+local"


@tool_call
def get_vault_info(
    config: AppConfig, *, rest_available: bool = False
) -> ToolResult:
    """Return vault metadata and server runtime info.

    `rest_available` is supplied by the server caller from the
    `RestAvailabilityDetector` it owns. Standalone callers (or tests)
    that don't pass it default to False.
    """
    note_count = sum(1 for _ in iter_markdown(config.vault_root))
    return ToolResult.success(
        data={
            "vault_root": str(config.vault_root),
            "note_count": note_count,
            "max_file_size_mb": config.max_file_size_mb,
            "max_batch": config.max_batch,
            "rest_available": rest_available,
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
        # Read
        {"name": "read_note", "kind": "read", "description": "Read a note's text content."},
        {"name": "list_notes", "kind": "read", "description": "List markdown notes."},
        {
            "name": "get_frontmatter",
            "kind": "read",
            "description": "Parse a note's YAML frontmatter (round-trip aware).",
        },
        {
            "name": "search_notes",
            "kind": "read",
            "description": "Search notes by literal query (fulltext + frontmatter).",
        },
        {
            "name": "resolve_wikilink",
            "kind": "read",
            "description": "Resolve a [[wikilink]] to a vault-relative path.",
        },
        # Write
        {
            "name": "create_note",
            "kind": "write",
            "description": "Create a new note. Fails if it already exists.",
        },
        {
            "name": "update_note",
            "kind": "write",
            "description": "Replace a note's full content.",
        },
        {
            "name": "append_to_note",
            "kind": "write",
            "description": "Append text to an existing note.",
        },
        {
            "name": "patch_note",
            "kind": "write",
            "description": "Literal find-replace on a note with explicit count check.",
        },
        # Frontmatter atomic
        {
            "name": "set_frontmatter_field",
            "kind": "write",
            "description": "Set a single frontmatter field, preserving everything else.",
        },
        {
            "name": "delete_frontmatter_field",
            "kind": "write",
            "description": "Delete a single frontmatter field.",
        },
        {
            "name": "merge_frontmatter",
            "kind": "write",
            "description": "Shallow or deep merge of a patch dict into the frontmatter.",
        },
        # Destructive (2-phase HMAC confirm)
        {
            "name": "delete_note",
            "kind": "destructive",
            "description": (
                "Delete a note. Two-phase HMAC confirm + snapshot under "
                ".ofmcp-trash/."
            ),
        },
        {
            "name": "rename_note",
            "kind": "destructive",
            "description": (
                "Rename a note within its folder. Two-phase confirm; "
                "optional best-effort wikilink rewrite."
            ),
        },
        {
            "name": "move_note",
            "kind": "destructive",
            "description": (
                "Move a note to another folder. Two-phase confirm; "
                "optional best-effort wikilink rewrite."
            ),
        },
        {
            "name": "execute_command",
            "kind": "destructive",
            "description": (
                "Execute a named Obsidian command via the Local REST API. "
                "Requires the plugin to be running. Two-phase HMAC confirm; "
                "the token is bound to the command id."
            ),
        },
        # Meta
        {"name": "get_vault_info", "kind": "meta", "description": "Vault metadata."},
        {
            "name": "list_tools_capabilities",
            "kind": "meta",
            "description": "Tools available on this server.",
        },
    ]
    return ToolResult.success(data={"tools": tools})
