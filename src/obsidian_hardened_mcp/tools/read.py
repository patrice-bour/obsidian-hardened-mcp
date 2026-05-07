# SPDX-License-Identifier: Apache-2.0
"""Read tools — `read_note`, `list_notes`, `get_frontmatter`.

(Frontmatter operations are added in M2; only `read_note` and `list_notes`
ship in M1.)
"""

from __future__ import annotations

from typing import Any

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode, ErrorInfo, ToolResult
from obsidian_hardened_mcp.domain.vault_path import VaultPath
from obsidian_hardened_mcp.fs.listing import iter_markdown
from obsidian_hardened_mcp.fs.reader import read_text
from obsidian_hardened_mcp.tools._base import map_exception, tool_call


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

    Forbidden directories (`.obsidian/`, `.git/`, `.trash/`, `.ohmcp-trash/`)
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


@tool_call
def read_multiple_notes(config: AppConfig, paths: list[str]) -> ToolResult:
    """Read N notes in one round-trip with partial-success semantics.

    Top-level rejection on empty input or `len(paths) > config.max_batch`.
    Otherwise iterates `paths` in order: per-path failures (path escape,
    not-found, file-too-large, etc.) are stored in `results[i].error`
    rather than aborting the call. If cumulative read bytes exceed
    `config.max_batch_bytes`, iteration stops; remaining paths are marked
    `BATCH_TOO_LARGE`.

    Note: `cumulative_bytes` in the response sums only the bytes of
    *successfully* read entries; per-path errors do not contribute to it.
    """
    if not paths:
        return ToolResult.failure(ErrorCode.INVALID_PATH, "paths cannot be empty")
    if len(paths) > config.max_batch:
        return ToolResult.failure(
            ErrorCode.BATCH_TOO_LARGE,
            f"{len(paths)} paths exceeds max_batch={config.max_batch}",
        )

    results: list[dict[str, Any]] = []
    cumulative_bytes = 0
    cap_hit_at: int | None = None

    for i, raw_path in enumerate(paths):
        if cap_hit_at is not None:
            results.append(
                {
                    "path": raw_path,
                    "error": {
                        "code": ErrorCode.BATCH_TOO_LARGE.value,
                        "message": (
                            f"cumulative size cap reached after index {cap_hit_at}"
                        ),
                    },
                }
            )
            continue

        try:
            vp = VaultPath.from_user(raw_path, config.vault_root)
            content = read_text(vp, max_size_bytes=config.max_file_size_bytes)
        except Exception as exc:
            err = map_exception(exc)
            err_info = err.error or ErrorInfo(
                code=ErrorCode.INTERNAL_ERROR, message=str(exc)
            )
            results.append(
                {
                    "path": raw_path,
                    "error": {
                        "code": err_info.code.value,
                        "message": err_info.message,
                    },
                }
            )
            continue

        size = len(content.encode("utf-8"))
        results.append({"path": raw_path, "content": content, "size": size})
        cumulative_bytes += size

        if cumulative_bytes > config.max_batch_bytes:
            cap_hit_at = i

    return ToolResult.success(
        data={
            "results": results,
            "cumulative_bytes": cumulative_bytes,
            "stopped_early": cap_hit_at is not None,
        }
    )
