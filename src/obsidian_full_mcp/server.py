"""MCP server registration.

`create_server(config)` builds a `FastMCP` instance wired to every tool
the server implements. The server runs over stdio.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.domain.results import ToolResult
from obsidian_full_mcp.rest.client import RestClient
from obsidian_full_mcp.rest.detector import RestAvailabilityDetector
from obsidian_full_mcp.security.audit_logger import AuditLogger
from obsidian_full_mcp.security.confirm import (
    ConfirmRegistry,
    load_or_bootstrap_secret,
)
from obsidian_full_mcp.tools.destructive import (
    delete_note as _delete_note_impl,
)
from obsidian_full_mcp.tools.destructive import (
    execute_command as _execute_command_impl,
)
from obsidian_full_mcp.tools.destructive import (
    move_note as _move_note_impl,
)
from obsidian_full_mcp.tools.destructive import (
    rename_note as _rename_note_impl,
)
from obsidian_full_mcp.tools.frontmatter import (
    delete_frontmatter_field as _delete_frontmatter_field_impl,
)
from obsidian_full_mcp.tools.frontmatter import (
    get_frontmatter as _get_frontmatter_impl,
)
from obsidian_full_mcp.tools.frontmatter import (
    merge_frontmatter as _merge_frontmatter_impl,
)
from obsidian_full_mcp.tools.frontmatter import (
    set_frontmatter_field as _set_frontmatter_field_impl,
)
from obsidian_full_mcp.tools.meta import get_vault_info as _get_vault_info_impl
from obsidian_full_mcp.tools.meta import (
    list_tools_capabilities as _list_tools_capabilities_impl,
)
from obsidian_full_mcp.tools.read import list_notes as _list_notes_impl
from obsidian_full_mcp.tools.read import read_note as _read_note_impl
from obsidian_full_mcp.tools.search import search_notes as _search_notes_impl
from obsidian_full_mcp.tools.wikilink import (
    resolve_wikilink as _resolve_wikilink_impl,
)
from obsidian_full_mcp.tools.write import (
    append_to_note as _append_to_note_impl,
)
from obsidian_full_mcp.tools.write import create_note as _create_note_impl
from obsidian_full_mcp.tools.write import patch_note as _patch_note_impl
from obsidian_full_mcp.tools.write import update_note as _update_note_impl
from obsidian_full_mcp.validation.config_loader import load_validation_config
from obsidian_full_mcp.validation.hooks import HookRegistry


def create_server(
    config: AppConfig,
    *,
    hooks: HookRegistry | None = None,
    registry: ConfirmRegistry | None = None,
    rest_detector: RestAvailabilityDetector | None = None,
) -> FastMCP:
    """Build a FastMCP server bound to the given configuration.

    `hooks` is the validation registry; if omitted the server loads it from
    `<vault_root>/.obsidian-full-mcp.yaml` (and falls back to an empty
    registry if the file is absent). Pass an explicit `HookRegistry([])`
    to skip auto-loading entirely (used by tests).

    `registry` is the 2-phase confirmation registry used by destructive
    tools. We construct it lazily on first destructive call so that
    purely-read flows (and tests for them) never touch the on-disk HMAC
    secret. Pass an explicit `ConfirmRegistry` to override (tests do this
    to avoid the secret file altogether).

    `rest_detector` enables the optional Local REST API integration. If
    omitted, we build one automatically when `config.rest_token` is set.
    Tests inject a fake detector (and indirectly a fake client) to
    avoid hitting a real Obsidian instance.
    """
    app = FastMCP(name="obsidian-full-mcp")
    audit = AuditLogger(audit_dir=config.audit_dir)
    if hooks is None:
        hooks = load_validation_config(config.vault_root)

    # Lazy registry construction: the secret is only loaded/bootstrapped
    # on the first destructive tool call. Mutable list as a closure cell.
    _registry_slot: list[ConfirmRegistry | None] = [registry]

    def _ensure_registry() -> ConfirmRegistry:
        if _registry_slot[0] is None:
            secret = load_or_bootstrap_secret(config.secret_file)
            _registry_slot[0] = ConfirmRegistry(secret=secret)
        return _registry_slot[0]  # type: ignore[return-value]

    # ---- REST client + detector (M7) -----------------------------------
    # When the caller injects a detector we trust them (tests do this).
    # Otherwise we build the pair iff a token is configured. With no
    # token there is no REST surface at all — execute_command will
    # short-circuit with REST_UNAVAILABLE and get_vault_info reports
    # rest_available=False.
    rest_client: RestClient | None = None
    if rest_detector is None and config.rest_token is not None:
        rest_client = RestClient(
            config.rest_url,
            config.rest_token,
            timeout_seconds=0.5,
        )
        rest_detector = RestAvailabilityDetector(rest_client, ttl_seconds=60)
    elif rest_detector is not None:
        # Tests pass a detector wrapping a fake client; expose the inner
        # client so execute_command can call it.
        rest_client = getattr(rest_detector, "_client", None)

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

    @app.tool(
        description=(
            "Search markdown notes for a literal query. mode='fulltext' "
            "scans bodies, 'frontmatter' scans frontmatter values, 'combined' "
            "(default) does both. Filter by folder, tag, or type."
        )
    )
    def search_notes(
        query: str,
        mode: str = "combined",
        folder: str | None = None,
        tag: str | None = None,
        type_filter: str | None = None,
        limit: int = 50,
    ) -> ToolResult:
        return _search_notes_impl(
            config,
            query,
            mode=mode,  # type: ignore[arg-type]
            folder=folder,
            tag=tag,
            type_filter=type_filter,
            limit=limit,
        )

    @app.tool(
        description=(
            "Resolve an Obsidian wikilink target (without [[...]]) to a "
            "vault-relative path. Returns alias/heading/block_id when "
            "present and `ambiguous=true` with `candidates` on collisions."
        )
    )
    def resolve_wikilink(target: str, from_path: str | None = None) -> ToolResult:
        return _resolve_wikilink_impl(config, target, from_path=from_path)

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
            from obsidian_full_mcp.domain.results import ErrorCode

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

    # ---- Destructive (2-phase HMAC confirm) ----------------------------

    @app.tool(
        description=(
            "Delete a note. Two-phase: first call returns a `confirm_token` "
            "and a preview without touching the disk; second call with the "
            "same token snapshots the file under `.ofmcp-trash/` and unlinks "
            "it. Pass `dry_run=True` to preview without issuing a token."
        )
    )
    def delete_note(
        path: str,
        confirm_token: str | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        return _delete_note_impl(
            config,
            audit,
            _ensure_registry(),
            path=path,
            confirm_token=confirm_token,
            dry_run=dry_run,
        )

    @app.tool(
        description=(
            "Rename a note within its current folder. `new_name` is a "
            "filename only (no slashes). Same 2-phase confirm as "
            "`delete_note`. Set `update_backlinks=True` to also rewrite "
            "`[[oldname]]` wikilinks across the vault (best-effort)."
        )
    )
    def rename_note(
        path: str,
        new_name: str,
        confirm_token: str | None = None,
        update_backlinks: bool = False,
        dry_run: bool = False,
    ) -> ToolResult:
        return _rename_note_impl(
            config,
            audit,
            _ensure_registry(),
            path=path,
            new_name=new_name,
            confirm_token=confirm_token,
            update_backlinks=update_backlinks,
            dry_run=dry_run,
        )

    @app.tool(
        description=(
            "Move a note to a different folder, keeping its filename. "
            "`new_folder` is a vault-relative folder path. Same 2-phase "
            "confirm as `delete_note`. `update_backlinks=True` is honoured "
            "but typically a no-op (basename unchanged)."
        )
    )
    def move_note(
        path: str,
        new_folder: str,
        confirm_token: str | None = None,
        update_backlinks: bool = False,
        dry_run: bool = False,
    ) -> ToolResult:
        return _move_note_impl(
            config,
            audit,
            _ensure_registry(),
            path=path,
            new_folder=new_folder,
            confirm_token=confirm_token,
            update_backlinks=update_backlinks,
            dry_run=dry_run,
        )

    @app.tool(
        description=(
            "Execute a named Obsidian command via the Local REST API plugin. "
            "Requires the plugin to be running and `OBSIDIAN_REST_TOKEN` set. "
            "Two-phase HMAC confirm (same protocol as delete_note); the token "
            "is bound to the command id."
        )
    )
    def execute_command(
        command_id: str,
        confirm_token: str | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        return _execute_command_impl(
            config,
            audit,
            _ensure_registry(),
            rest_client,
            rest_detector,
            command_id=command_id,
            confirm_token=confirm_token,
            dry_run=dry_run,
        )

    # ---- Meta ----------------------------------------------------------

    @app.tool(description="Return vault metadata (root, note count, limits, server identity).")
    def get_vault_info() -> ToolResult:
        rest_available = (
            rest_detector.is_available() if rest_detector is not None else False
        )
        return _get_vault_info_impl(config, rest_available=rest_available)

    @app.tool(description="Return the manifest of tools available on this server.")
    def list_tools_capabilities() -> ToolResult:
        return _list_tools_capabilities_impl(config)

    return app
