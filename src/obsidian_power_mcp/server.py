"""MCP server registration.

`create_server(config)` builds a `FastMCP` instance wired to every tool
the server implements. The server runs over stdio.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ToolResult
from obsidian_power_mcp.security.audit_logger import AuditLogger
from obsidian_power_mcp.tools.frontmatter import (
    delete_frontmatter_field as _delete_frontmatter_field_impl,
)
from obsidian_power_mcp.tools.frontmatter import (
    get_frontmatter as _get_frontmatter_impl,
)
from obsidian_power_mcp.tools.frontmatter import (
    merge_frontmatter as _merge_frontmatter_impl,
)
from obsidian_power_mcp.tools.frontmatter import (
    set_frontmatter_field as _set_frontmatter_field_impl,
)
from obsidian_power_mcp.tools.meta import get_vault_info as _get_vault_info_impl
from obsidian_power_mcp.tools.meta import (
    list_tools_capabilities as _list_tools_capabilities_impl,
)
from obsidian_power_mcp.tools.read import list_notes as _list_notes_impl
from obsidian_power_mcp.tools.read import read_note as _read_note_impl
from obsidian_power_mcp.tools.write import (
    append_to_note as _append_to_note_impl,
)
from obsidian_power_mcp.tools.write import create_note as _create_note_impl
from obsidian_power_mcp.tools.write import patch_note as _patch_note_impl
from obsidian_power_mcp.tools.write import update_note as _update_note_impl
from obsidian_power_mcp.validation.config_loader import load_validation_config
from obsidian_power_mcp.validation.hooks import HookRegistry


def create_server(
    config: AppConfig, *, hooks: HookRegistry | None = None
) -> FastMCP:
    """Build a FastMCP server bound to the given configuration.

    `hooks` is the validation registry; if omitted the server loads it from
    `<vault_root>/.obsidian-power-mcp.yaml` (and falls back to an empty
    registry if the file is absent). Pass an explicit `HookRegistry([])`
    to skip auto-loading entirely (used by tests).
    """
    app = FastMCP(name="obsidian-power-mcp")
    audit = AuditLogger(audit_dir=config.audit_dir)
    if hooks is None:
        hooks = load_validation_config(config.vault_root)

    # ---- Read ----------------------------------------------------------

    @app.tool(description="Read a note's full text content from the vault.")
    def read_note(path: str) -> ToolResult:
        return _read_note_impl(config, path)

    @app.tool(description="List markdown notes in the vault, optionally filtered by folder.")
    def list_notes(folder: str | None = None, limit: int = 200) -> ToolResult:
        return _list_notes_impl(config, folder=folder, limit=limit)

    @app.tool(
        description=(
            "Return the parsed YAML frontmatter of a note plus a preview of the body."
        )
    )
    def get_frontmatter(path: str) -> ToolResult:
        return _get_frontmatter_impl(config, path)

    # ---- Write ---------------------------------------------------------

    @app.tool(description="Create a new note. Fails if the file already exists.")
    def create_note(path: str, content: str, dry_run: bool = False) -> ToolResult:
        return _create_note_impl(
            config, audit, path, content, hooks=hooks, dry_run=dry_run
        )

    @app.tool(description="Replace a note's full content. Fails if the file does not exist.")
    def update_note(path: str, content: str, dry_run: bool = False) -> ToolResult:
        return _update_note_impl(
            config, audit, path, content, hooks=hooks, dry_run=dry_run
        )

    @app.tool(description="Append text to an existing note (with optional separating newline).")
    def append_to_note(
        path: str,
        content: str,
        ensure_newline: bool = True,
        dry_run: bool = False,
    ) -> ToolResult:
        return _append_to_note_impl(
            config,
            audit,
            path,
            content,
            hooks=hooks,
            ensure_newline=ensure_newline,
            dry_run=dry_run,
        )

    @app.tool(
        description=(
            "Literal find-replace on a note. `count=1` (default) requires exactly one "
            "occurrence; `count=0` replaces all occurrences; any other positive integer "
            "is the EXACT number of matches expected."
        )
    )
    def patch_note(
        path: str,
        find: str,
        replace: str,
        count: int = 1,
        dry_run: bool = False,
    ) -> ToolResult:
        return _patch_note_impl(
            config,
            audit,
            path,
            find,
            replace,
            hooks=hooks,
            count=count,
            dry_run=dry_run,
        )

    # ---- Frontmatter atomic --------------------------------------------

    @app.tool(
        description=(
            "Set a single frontmatter field, creating the block if absent. "
            "Round-trip preserves comments, key order and quote style of other fields."
        )
    )
    def set_frontmatter_field(
        path: str, key: str, value: Any, dry_run: bool = False
    ) -> ToolResult:
        return _set_frontmatter_field_impl(
            config, audit, path, key, value, hooks=hooks, dry_run=dry_run
        )

    @app.tool(description="Delete a single frontmatter field.")
    def delete_frontmatter_field(
        path: str, key: str, dry_run: bool = False
    ) -> ToolResult:
        return _delete_frontmatter_field_impl(
            config, audit, path, key, hooks=hooks, dry_run=dry_run
        )

    @app.tool(
        description=(
            "Merge a patch dict into the frontmatter. mode='shallow' replaces "
            "top-level keys; mode='deep' recurses into nested mappings."
        )
    )
    def merge_frontmatter(
        path: str,
        patch: dict[str, Any],
        mode: str = "shallow",
        dry_run: bool = False,
    ) -> ToolResult:
        if mode not in ("shallow", "deep"):
            from obsidian_power_mcp.domain.results import ErrorCode

            return ToolResult.failure(
                ErrorCode.INVALID_PATH, f"unknown merge mode: {mode!r}"
            )
        return _merge_frontmatter_impl(
            config,
            audit,
            path,
            patch,
            mode=mode,  # type: ignore[arg-type]
            hooks=hooks,
            dry_run=dry_run,
        )

    # ---- Meta ----------------------------------------------------------

    @app.tool(description="Return vault metadata (root, note count, limits, server identity).")
    def get_vault_info() -> ToolResult:
        return _get_vault_info_impl(config)

    @app.tool(description="Return the manifest of tools available on this server.")
    def list_tools_capabilities() -> ToolResult:
        return _list_tools_capabilities_impl(config)

    return app
