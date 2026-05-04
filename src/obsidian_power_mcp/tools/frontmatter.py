"""Frontmatter tools.

`get_frontmatter` ships in M2; the atomic field operations
(`set_frontmatter_field`, `delete_frontmatter_field`, `merge_frontmatter`)
land in M3 alongside the atomic writer.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ToolResult
from obsidian_power_mcp.domain.vault_path import VaultPath
from obsidian_power_mcp.frontmatter import parse_note
from obsidian_power_mcp.fs.reader import read_text
from obsidian_power_mcp.tools._base import tool_call

_BODY_PREVIEW_BYTES = 4096


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
