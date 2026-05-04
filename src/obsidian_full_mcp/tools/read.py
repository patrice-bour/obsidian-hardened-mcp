"""Read tools — `read_note`, `list_notes`, `get_frontmatter`.

(Frontmatter operations are added in M2; only `read_note` and `list_notes`
ship in M1.)
"""

from __future__ import annotations

from typing import Any

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.domain.results import ToolResult
from obsidian_full_mcp.domain.vault_path import VaultPath
from obsidian_full_mcp.fs.listing import iter_markdown
from obsidian_full_mcp.fs.reader import read_text
from obsidian_full_mcp.tools._base import tool_call


@tool_call
def read_note(config: AppConfig, path: str) -> ToolResult:
    """Return the full text of a note as UTF-8."""
    vp = VaultPath.from_user(path, config.vault_root)
    content = read_text(vp, max_size_bytes=config.max_file_size_bytes)
    return ToolResult.success(
        data={
            "path": str(vp.relative),
            "content": content,
            "size": len(content.encode("utf-8")),
        }
    )


@tool_call
def list_notes(
    config: AppConfig,
    folder: str | None = None,
    limit: int = 200,
) -> ToolResult:
    """List markdown notes under the vault, optionally filtered by folder.

    Forbidden directories (`.obsidian/`, `.git/`, `.trash/`, `.ofmcp-trash/`)
    are pruned from the traversal — they are never visible to clients.
    """
    if limit <= 0 or limit > config.max_batch:
        limit = min(max(limit, 1), config.max_batch)

    if folder is None:
        scan_root = config.vault_root
        prefix = ""
    else:
        vp = VaultPath.from_user(folder, config.vault_root)
        scan_root = vp.absolute
        prefix = str(vp.relative) + "/"

    notes = sorted(
        md.relative_to(config.vault_root).as_posix() for md in iter_markdown(scan_root)
    )
    if folder is not None:
        notes = [n for n in notes if n.startswith(prefix)]

    truncated = len(notes) > limit
    if truncated:
        notes = notes[:limit]

    data: dict[str, Any] = {"notes": notes, "truncated": truncated, "limit": limit}
    return ToolResult.success(data=data)
