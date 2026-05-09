# SPDX-License-Identifier: Apache-2.0
"""MCP server registration.

`create_server(config)` builds a `FastMCP` instance wired to every tool
the server implements. The server runs over stdio.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from obsidian_hardened_mcp.config import AppConfig, TrashPolicy
from obsidian_hardened_mcp.domain.results import ErrorCode, ToolResult
from obsidian_hardened_mcp.fs.pruner import prune_trash
from obsidian_hardened_mcp.rest.client import RestClient
from obsidian_hardened_mcp.rest.detector import RestAvailabilityDetector
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.security.confirm import (
    ConfirmRegistry,
    load_or_bootstrap_secret,
)
from obsidian_hardened_mcp.tools.destructive import (
    delete_note as _delete_note_impl,
)
from obsidian_hardened_mcp.tools.destructive import (
    execute_command as _execute_command_impl,
)
from obsidian_hardened_mcp.tools.destructive import (
    move_note as _move_note_impl,
)
from obsidian_hardened_mcp.tools.destructive import (
    rename_note as _rename_note_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    delete_frontmatter_field as _delete_frontmatter_field_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    get_frontmatter as _get_frontmatter_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    manage_tags as _manage_tags_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    merge_frontmatter as _merge_frontmatter_impl,
)
from obsidian_hardened_mcp.tools.frontmatter import (
    set_frontmatter_field as _set_frontmatter_field_impl,
)
from obsidian_hardened_mcp.tools.meta import get_vault_info as _get_vault_info_impl
from obsidian_hardened_mcp.tools.meta import (
    list_tools_capabilities as _list_tools_capabilities_impl,
)
from obsidian_hardened_mcp.tools.read import list_notes as _list_notes_impl
from obsidian_hardened_mcp.tools.read import read_multiple_notes as _read_multiple_notes_impl
from obsidian_hardened_mcp.tools.read import read_note as _read_note_impl
from obsidian_hardened_mcp.tools.search import search_notes as _search_notes_impl
from obsidian_hardened_mcp.tools.wikilink import (
    resolve_wikilink as _resolve_wikilink_impl,
)
from obsidian_hardened_mcp.tools.write import (
    append_to_note as _append_to_note_impl,
)
from obsidian_hardened_mcp.tools.write import create_note as _create_note_impl
from obsidian_hardened_mcp.tools.write import patch_note as _patch_note_impl
from obsidian_hardened_mcp.tools.write import update_note as _update_note_impl
from obsidian_hardened_mcp.validation.config_loader import (
    load_trash_policy,
    load_validation_config,
)
from obsidian_hardened_mcp.validation.hooks import HookRegistry


class _ConfirmDestructive(BaseModel):
    """User-facing schema for ctx.elicit confirmation prompt (M6-11)."""

    confirm: bool = Field(
        description="Confirm the destructive operation",
    )


@dataclass(frozen=True, slots=True)
class _ElicitOutcome:
    """Result of `_run_elicit_gate`."""

    accepted: bool
    error_code: ErrorCode | None
    error_message: str | None = None


async def _run_elicit_gate(
    ctx: Any, *, message: str, config: AppConfig
) -> _ElicitOutcome:
    """Ask the MCP client to confirm a destructive operation via
    `ctx.elicit`.

    Returns:
        `_ElicitOutcome(accepted=True, ...)` if the user accepted (or
        if the client lacks elicit support AND
        `config.require_elicitation` is False);
        `_ElicitOutcome(accepted=False, error_code=...)` otherwise.

    The caller decides what to do with a non-accept outcome (typically
    return `ToolResult.failure(outcome.error_code, ...)`).
    """
    try:
        result = await ctx.elicit(
            message=message,
            schema=_ConfirmDestructive,
        )
    except Exception as exc:  # client lacks elicit support, or transport error
        if not config.require_elicitation:
            return _ElicitOutcome(accepted=True, error_code=None)
        return _ElicitOutcome(
            accepted=False,
            error_code=ErrorCode.ELICITATION_UNSUPPORTED,
            error_message=f"client does not support Context.elicit: {exc}",
        )

    accepted = (
        getattr(result, "action", None) == "accept"
        and getattr(result, "data", None) is not None
        and bool(getattr(result.data, "confirm", False))
    )
    if accepted:
        return _ElicitOutcome(accepted=True, error_code=None)
    return _ElicitOutcome(
        accepted=False,
        error_code=ErrorCode.ELICITATION_REJECTED,
        error_message="user declined the destructive operation",
    )


def create_server(
    config: AppConfig,
    *,
    hooks: HookRegistry | None = None,
    registry: ConfirmRegistry | None = None,
    rest_detector: RestAvailabilityDetector | None = None,
    trash_policy: TrashPolicy | None = None,
) -> FastMCP:
    """Build a FastMCP server bound to the given configuration.

    `hooks` is the validation registry; if omitted the server loads it from
    `<vault_root>/.obsidian-hardened-mcp.yaml` (and falls back to an empty
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

    `trash_policy` controls auto-cleanup of `.ohmcp-trash/`. When
    omitted, the server reads the ``trash:`` block from
    `<vault_root>/.obsidian-hardened-mcp.yaml` (falling back to
    `config.trash_policy`, itself the `TrashPolicy()` default). Tests
    that bypass YAML loading by injecting `hooks` will fall through to
    `config.trash_policy` here as well.
    """
    app = FastMCP(name="obsidian-hardened-mcp")
    audit = AuditLogger(audit_dir=config.audit_dir)
    if hooks is None:
        hooks = load_validation_config(config.vault_root)
        if trash_policy is None:
            trash_policy = load_trash_policy(config.vault_root)
    if trash_policy is None:
        trash_policy = config.trash_policy

    # Startup prune: one synchronous sweep of `.ohmcp-trash/` so a
    # long-accumulated backlog gets cleaned at boot. No-op when the
    # trash dir doesn't exist or no candidates qualify. Suppress any
    # exception so a misbehaving prune (rare: I/O during teardown,
    # exotic FS) never blocks server startup.
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        prune_trash(config.vault_root, trash_policy, audit, trigger="startup")

    def _maybe_post_op_prune(result: ToolResult) -> None:
        """Run a defensive trash sweep after a successful destructive op.

        Only runs on phase-2 success (``ok=True``, ``dry_run=False``);
        phase-1 returns and dry-run probes leave nothing new to clean.
        Failures here never propagate — the prune is best-effort and
        the destructive op already succeeded by the time we get called.
        """
        if not result.ok or result.dry_run:
            return
        with contextlib.suppress(Exception):  # pragma: no cover - defensive
            prune_trash(
                config.vault_root, trash_policy, audit, trigger="post_op"
            )

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
            "Read multiple notes in one batch with partial-success "
            "semantics. Per-path errors live in results[i].error; "
            "cumulative byte cap stops iteration."
        )
    )
    def read_multiple_notes(paths: list[str]) -> ToolResult:
        return _read_multiple_notes_impl(config, paths)

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
            from obsidian_hardened_mcp.domain.results import ErrorCode

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

    @app.tool(
        description=(
            "Add, remove, replace, or list tags in a note's YAML "
            "frontmatter. Idempotent: 'add' dedupes silently, 'remove' "
            "no-ops on absent tags, empty result drops the 'tags:' key. "
            "Input '#tag' is normalised to 'tag'."
        )
    )
    def manage_tags(
        path: str,
        op: str,
        tags: list[str] | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        return _manage_tags_impl(
            config,
            audit,
            path,
            op,  # type: ignore[arg-type]
            tags,
            hooks=hooks,
            dry_run=dry_run,
        )

    # ---- Destructive (2-phase HMAC confirm) ----------------------------

    @app.tool(
        description=(
            "Delete a note from the vault. Two-phase confirmation: "
            "the first call returns a token; passing it back on the "
            "same token snapshots the file under `.ohmcp-trash/` and "
            "unlinks it. Pass `dry_run=True` to preview without "
            "issuing a token. Phase 2 also requires user confirmation "
            "via the client UI (M6-11)."
        )
    )
    async def delete_note(
        path: str,
        confirm_token: str | None = None,
        dry_run: bool = False,
        ctx: Context = None,  # type: ignore[assignment,type-arg]
    ) -> ToolResult:
        # M6-11: out-of-band confirmation gate at Phase 2 (real, not dry).
        is_phase2 = confirm_token is not None and not dry_run
        if is_phase2:
            outcome = await _run_elicit_gate(
                ctx,
                message=f"Confirm delete on {path}?",
                config=config,
            )
            if not outcome.accepted:
                return ToolResult.failure(
                    outcome.error_code,  # type: ignore[arg-type]
                    outcome.error_message or "elicitation refused",
                )
        result = _delete_note_impl(
            config,
            audit,
            _ensure_registry(),
            path=path,
            confirm_token=confirm_token,
            dry_run=dry_run,
        )
        _maybe_post_op_prune(result)
        return result

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
        result = _rename_note_impl(
            config,
            audit,
            _ensure_registry(),
            path=path,
            new_name=new_name,
            confirm_token=confirm_token,
            update_backlinks=update_backlinks,
            dry_run=dry_run,
        )
        _maybe_post_op_prune(result)
        return result

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
        result = _move_note_impl(
            config,
            audit,
            _ensure_registry(),
            path=path,
            new_folder=new_folder,
            confirm_token=confirm_token,
            update_backlinks=update_backlinks,
            dry_run=dry_run,
        )
        _maybe_post_op_prune(result)
        return result

    @app.tool(
        description=(
            "Execute a named Obsidian command via the Local REST API plugin. "
            "Requires the plugin to be running and `OBSIDIAN_REST_TOKEN` set. "
            "Two-phase confirmation + Phase 2 requires user confirmation "
            "via the client UI (M6-11)."
        )
    )
    async def execute_command(
        command_id: str,
        confirm_token: str | None = None,
        dry_run: bool = False,
        ctx: Context = None,  # type: ignore[assignment,type-arg]
    ) -> ToolResult:
        # M6-11: out-of-band confirmation gate at Phase 2 (real, not dry).
        is_phase2 = confirm_token is not None and not dry_run
        if is_phase2:
            outcome = await _run_elicit_gate(
                ctx,
                message=f"Confirm Obsidian command '{command_id}'?",
                config=config,
            )
            if not outcome.accepted:
                return ToolResult.failure(
                    outcome.error_code,  # type: ignore[arg-type]
                    outcome.error_message or "elicitation refused",
                )
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
