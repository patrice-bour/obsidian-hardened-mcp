"""Frontmatter tools.

`get_frontmatter` (M2) is read-only.

Atomic field operations (`set_frontmatter_field`, `delete_frontmatter_field`,
`merge_frontmatter`) ship in M3: they parse, mutate the round-trip-aware
`CommentedMap`, render, and atomically write back through `fs.writer`.

Round-trip preservation means edits to ONE field leave comments, key order,
indentation and quote styles of OTHER fields untouched. That is the headline
gap left open by every other Obsidian MCP server we surveyed.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any, Literal

from ruamel.yaml.comments import CommentedMap

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode, ToolResult
from obsidian_power_mcp.domain.vault_path import VaultPath
from obsidian_power_mcp.frontmatter import (
    ParsedNote,
    parse_note,
    render_note,
)
from obsidian_power_mcp.fs.reader import read_text
from obsidian_power_mcp.security.audit_logger import AuditLogger
from obsidian_power_mcp.tools._base import map_exception, tool_call
from obsidian_power_mcp.tools.write import _emit, _params_hash

_BODY_PREVIEW_BYTES = 4096

MergeMode = Literal["shallow", "deep"]


@tool_call
def get_frontmatter(config: AppConfig, path: str) -> ToolResult:
    """Return the parsed frontmatter and a preview of the body.

    Dates and datetimes are serialised to ISO-8601 strings so the result is
    JSON-clean. The full body is *not* returned — call `read_note` for that.
    """
    vp = VaultPath.from_user(path, config.vault_root)
    text = read_text(vp, max_size_bytes=config.max_file_size_bytes)
    parsed = parse_note(text)

    fm_dict: dict[str, Any] | None = (
        None
        if parsed.frontmatter is None
        else _to_json_safe(dict(parsed.frontmatter))
    )

    body = parsed.body
    body_preview = body[:_BODY_PREVIEW_BYTES]

    return ToolResult.success(
        data={
            "path": str(vp.relative),
            "has_frontmatter": parsed.frontmatter is not None,
            "frontmatter": fm_dict,
            "body_preview": body_preview,
            "body_truncated": len(body) > _BODY_PREVIEW_BYTES,
        }
    )


def set_frontmatter_field(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    key: str,
    value: Any,
    *,
    dry_run: bool = False,
) -> ToolResult:
    """Set a single frontmatter field, creating the block if absent.

    Round-trip preservation: comments, key order, quote styles of OTHER
    fields are kept exactly. Only the targeted key is added/overwritten.
    """
    return _mutate_frontmatter(
        config,
        audit,
        path,
        tool_name="set_frontmatter_field",
        params=(key, value),
        dry_run=dry_run,
        mutator=lambda fm: _set_field(fm, key, value),
    )


def delete_frontmatter_field(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    key: str,
    *,
    dry_run: bool = False,
) -> ToolResult:
    """Delete a single frontmatter field. Returns FIELD_NOT_FOUND if missing."""
    return _mutate_frontmatter(
        config,
        audit,
        path,
        tool_name="delete_frontmatter_field",
        params=(key,),
        dry_run=dry_run,
        mutator=lambda fm: _delete_field(fm, key),
    )


def merge_frontmatter(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    patch: dict[str, Any],
    *,
    mode: MergeMode = "shallow",
    dry_run: bool = False,
) -> ToolResult:
    """Merge a patch dict into the frontmatter.

    `mode="shallow"` (default): top-level keys replace existing ones outright;
        nested mappings are NOT recursed into.
    `mode="deep"`: nested dict-vs-dict merges recurse; lists and scalars are
        replaced wholesale.
    """
    return _mutate_frontmatter(
        config,
        audit,
        path,
        tool_name="merge_frontmatter",
        params=(patch, mode),
        dry_run=dry_run,
        mutator=lambda fm: _merge(fm, patch, mode),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _mutate_frontmatter(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    *,
    tool_name: str,
    params: tuple[Any, ...],
    dry_run: bool,
    mutator: Callable[[CommentedMap | None], CommentedMap],
) -> ToolResult:
    started = time.monotonic()
    try:
        vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)
    if not vp.absolute.exists():
        return ToolResult.failure(
            ErrorCode.NOT_FOUND, f"file not found: {vp.relative}"
        )

    try:
        existing = read_text(vp, max_size_bytes=config.max_file_size_bytes)
        parsed = parse_note(existing)
        new_fm = mutator(parsed.frontmatter)
    except _FieldNotFoundError as exc:
        return ToolResult.failure(ErrorCode.FIELD_NOT_FOUND, str(exc))
    except Exception as exc:
        return map_exception(exc)

    new_parsed = ParsedNote(frontmatter=new_fm, body=parsed.body)
    new_content = render_note(new_parsed)

    if dry_run:
        audit_id = _emit(
            audit,
            tool=tool_name,
            op_kind="write",
            vault_path=str(vp.relative),
            outcome="success",
            started=started,
            params_hash=_params_hash(path, *params),
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                "path": str(vp.relative),
                "new_content": new_content,
                "new_frontmatter": _to_json_safe(dict(new_fm)) if new_fm else None,
            },
            dry_run=True,
            audit_id=audit_id,
        )

    from obsidian_power_mcp.fs.writer import atomic_write_text

    try:
        atomic_write_text(vp, new_content)
    except Exception as exc:
        return map_exception(exc)

    audit_id = _emit(
        audit,
        tool=tool_name,
        op_kind="write",
        vault_path=str(vp.relative),
        outcome="success",
        started=started,
        params_hash=_params_hash(path, *params),
        dry_run=False,
    )
    return ToolResult(
        ok=True,
        data={
            "path": str(vp.relative),
            "new_frontmatter": _to_json_safe(dict(new_fm)) if new_fm else None,
        },
        audit_id=audit_id,
    )


class _FieldNotFoundError(Exception):
    """Internal sentinel used by `_delete_field`."""


def _set_field(fm: CommentedMap | None, key: str, value: Any) -> CommentedMap:
    if fm is None:
        fm = CommentedMap()
    fm[key] = value
    return fm


def _delete_field(fm: CommentedMap | None, key: str) -> CommentedMap:
    if fm is None or key not in fm:
        raise _FieldNotFoundError(f"field {key!r} not found")
    del fm[key]
    return fm


def _merge(
    fm: CommentedMap | None, patch: dict[str, Any], mode: MergeMode
) -> CommentedMap:
    if fm is None:
        fm = CommentedMap()
    if mode == "shallow":
        for k, v in patch.items():
            fm[k] = v
    else:  # deep
        _deep_merge_into(fm, patch)
    return fm


def _deep_merge_into(target: CommentedMap, patch: dict[str, Any]) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_merge_into(target[k], v)
        else:
            target[k] = v


def _to_json_safe(value: Any) -> Any:
    """Recursively coerce ruamel/CommentedMap values to JSON-clean Python.

    - `datetime.date` / `datetime.datetime` -> ISO-8601 string
    - mappings -> dict
    - sequences -> list
    - everything else passed through (str, int, float, bool, None)
    """
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(item) for item in value]
    return value
