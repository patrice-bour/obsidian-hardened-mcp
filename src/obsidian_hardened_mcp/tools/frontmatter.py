# SPDX-License-Identifier: Apache-2.0
"""Frontmatter tools.

`get_frontmatter` (M2) is read-only.

Atomic field operations (`set_frontmatter_field`, `delete_frontmatter_field`,
`merge_frontmatter`) ship in M3: they parse, mutate the round-trip-aware
`CommentedMap`, render, and atomically write back through `fs.writer`.

Round-trip preservation means edits to ONE field leave comments, key order,
indentation and quote styles of OTHER fields untouched. That is the headline
gap left open by every other Obsidian MCP server we surveyed.

Write-side safety: every value flowing into the frontmatter is checked
against a strict type whitelist (`_ensure_safe_value`). This closes the
loop with `frontmatter.parser._reject_custom_tags` — we refuse on read AND
on write any construct that could become an unsafe YAML tag downstream.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import time
from collections.abc import Callable, Iterable
from typing import Any, Literal

from ruamel.yaml.comments import CommentedMap

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode, ToolResult
from obsidian_hardened_mcp.domain.vault_path import VaultPath
from obsidian_hardened_mcp.frontmatter import (
    ParsedNote,
    parse_note,
    render_note,
)
from obsidian_hardened_mcp.fs.reader import read_text
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools._base import (
    emit_audit,
    map_exception,
    new_request_id,
    params_hash,
    run_validation_hooks,
    to_plain_dict,
    tool_call,
)
from obsidian_hardened_mcp.validation.hooks import HookContext, HookRegistry

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
    hooks: HookRegistry | None = None,
    dry_run: bool = False,
) -> ToolResult:
    """Set a single frontmatter field, creating the block if absent.

    Round-trip preservation: comments, key order, quote styles of OTHER
    fields are kept exactly. Only the targeted key is added/overwritten.

    Raises (returned as `ToolResult.failure`):
        UNSAFE_YAML if `value` contains a non-whitelisted type.
    """
    try:
        _ensure_safe_value(value)
    except _UnsafeValueError as exc:
        return ToolResult.failure(ErrorCode.UNSAFE_YAML, str(exc))
    return _mutate_frontmatter(
        config,
        audit,
        path,
        tool_name="set_frontmatter_field",
        params=(key, value),
        dry_run=dry_run,
        hooks=hooks,
        mutator=lambda fm: _set_field(fm, key, value),
    )


def delete_frontmatter_field(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    key: str,
    *,
    hooks: HookRegistry | None = None,
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
        hooks=hooks,
        mutator=lambda fm: _delete_field(fm, key),
    )


def merge_frontmatter(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    patch: dict[str, Any],
    *,
    mode: MergeMode = "shallow",
    hooks: HookRegistry | None = None,
    dry_run: bool = False,
) -> ToolResult:
    """Merge a patch dict into the frontmatter.

    `mode="shallow"`: top-level keys replace existing ones outright;
        nested mappings are NOT recursed into.
    `mode="deep"`: nested dict-vs-dict merges recurse; lists, scalars, and
        type mismatches (dict-vs-list, dict-vs-None, etc.) are replaced
        wholesale at the offending key.

    Raises (returned as `ToolResult.failure`):
        UNSAFE_YAML if `patch` contains a non-whitelisted type.
    """
    try:
        _ensure_safe_value(patch)
    except _UnsafeValueError as exc:
        return ToolResult.failure(ErrorCode.UNSAFE_YAML, str(exc))
    return _mutate_frontmatter(
        config,
        audit,
        path,
        tool_name="merge_frontmatter",
        params=(patch, mode),
        dry_run=dry_run,
        hooks=hooks,
        mutator=lambda fm: _merge(fm, patch, mode),
    )


TagOp = Literal["add", "remove", "replace", "list"]


@tool_call
def manage_tags(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    op: TagOp,
    tags: list[str] | None = None,
    *,
    hooks: HookRegistry | None = None,
    dry_run: bool = False,
) -> ToolResult:
    """Add, remove, replace, or list tags in a note's YAML frontmatter.

    `op="add"`: idempotent — duplicates dropped silently.
    `op="remove"`: silent no-op for tags that aren't present; if the
      result is empty, the `tags:` key is removed from the frontmatter.
    `op="replace"`: wholesale set; pass `tags=[]` to clear.
    `op="list"`: read-only, no audit emission.

    Input tags are normalised: leading '#' stripped, whitespace trimmed,
    validated against `^[A-Za-z0-9_./-]+$` with no leading/trailing '/'.
    """
    if op in ("add", "remove") and (tags is None or not tags):
        return ToolResult.failure(
            ErrorCode.INVALID_TAG,
            f"op={op!r} requires non-empty tags",
        )

    if tags is None:
        normalized: list[str] = []
    else:
        try:
            normalized = _dedupe_in_order(_normalize_tag(t) for t in tags)
        except _InvalidTagError as exc:
            return ToolResult.failure(ErrorCode.INVALID_TAG, str(exc))

    started = time.monotonic()
    request_id = new_request_id()

    try:
        vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)
    if not vp.absolute.exists():
        return ToolResult.failure(
            ErrorCode.NOT_FOUND, f"file not found: {vp.relative}"
        )

    try:
        existing_text = read_text(vp, max_size_bytes=config.max_file_size_bytes)
        parsed = parse_note(existing_text)
        existing_tags = _extract_existing_tags(parsed.frontmatter)
    except _MalformedTagsFieldError as exc:
        return ToolResult.failure(ErrorCode.MALFORMED_FRONTMATTER, str(exc))
    except Exception as exc:
        return map_exception(exc)

    if op == "list":
        return ToolResult.success(
            data={
                "path": str(vp.relative),
                "tags": list(existing_tags),
            }
        )

    # Compute the new tag list per op.
    if op == "add":
        new_tags = list(existing_tags)
        for t in normalized:
            if t not in new_tags:
                new_tags.append(t)
    elif op == "remove":
        raise NotImplementedError("op='remove' lands in Task 5")
    elif op == "replace":
        raise NotImplementedError("op='replace' lands in Task 6")
    else:  # pragma: no cover - exhaustive Literal
        return ToolResult.failure(  # type: ignore[unreachable]
            ErrorCode.INVALID_TAG, f"unknown op {op!r}"
        )

    added = [t for t in new_tags if t not in existing_tags]
    removed = [t for t in existing_tags if t not in new_tags]
    params_hash_value = params_hash(path, op, normalized)

    # Skip disk write on no-op (mtime stability).
    if new_tags == existing_tags:
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool="manage_tags",
            op_kind="write",
            vault_path=str(vp.relative),
            outcome="success",
            started=started,
            params_hash=params_hash_value,
            dry_run=dry_run,
        )
        return ToolResult(
            ok=True,
            data={
                "path": str(vp.relative),
                "request_id": request_id,
                "op": op,
                "tags": new_tags,
                "added": added,
                "removed": removed,
            },
            dry_run=dry_run,
            audit_id=audit_id,
        )

    # Build new frontmatter.
    new_fm = (
        copy.deepcopy(parsed.frontmatter)
        if parsed.frontmatter is not None
        else CommentedMap()
    )
    if not new_tags:
        if "tags" in new_fm:
            del new_fm["tags"]
    else:
        new_fm["tags"] = new_tags

    new_parsed = ParsedNote(
        frontmatter=(new_fm if new_fm else None),
        body=parsed.body,
    )
    new_content = render_note(new_parsed)

    # Hooks run on the post-write state.
    if hooks is not None:
        try:
            run_validation_hooks(
                hooks,
                HookContext(
                    path=vp,
                    new_frontmatter=(
                        None if not new_fm else to_plain_dict(dict(new_fm))
                    ),
                    new_body=parsed.body,
                    operation="manage_tags",
                ),
            )
        except Exception as exc:
            return map_exception(exc)

    if dry_run:
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool="manage_tags",
            op_kind="write",
            vault_path=str(vp.relative),
            outcome="success",
            started=started,
            params_hash=params_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                "path": str(vp.relative),
                "request_id": request_id,
                "op": op,
                "tags": new_tags,
                "added": added,
                "removed": removed,
                "new_content": new_content,
            },
            dry_run=True,
            audit_id=audit_id,
        )

    from obsidian_hardened_mcp.fs.writer import atomic_write_text

    try:
        atomic_write_text(vp, new_content)
    except Exception as exc:
        return map_exception(exc)

    audit_id = emit_audit(
        audit,
        request_id=request_id,
        tool="manage_tags",
        op_kind="write",
        vault_path=str(vp.relative),
        outcome="success",
        started=started,
        params_hash=params_hash_value,
        dry_run=False,
    )
    return ToolResult(
        ok=True,
        data={
            "path": str(vp.relative),
            "request_id": request_id,
            "op": op,
            "tags": new_tags,
            "added": added,
            "removed": removed,
        },
        audit_id=audit_id,
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
    hooks: HookRegistry | None,
    mutator: Callable[[CommentedMap | None], CommentedMap],
) -> ToolResult:
    started = time.monotonic()
    request_id = new_request_id()
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
        # `dry_run` must NOT mutate the in-memory `parsed.frontmatter`. Copy
        # so the mutator works on its own object regardless of mode; this
        # also keeps real-write behaviour predictable since the original
        # parse result is never trampled.
        fm_to_mutate = (
            None if parsed.frontmatter is None else copy.deepcopy(parsed.frontmatter)
        )
        new_fm = mutator(fm_to_mutate)
    except _FieldNotFoundError as exc:
        return ToolResult.failure(ErrorCode.FIELD_NOT_FOUND, str(exc))
    except Exception as exc:
        return map_exception(exc)

    new_parsed = ParsedNote(frontmatter=new_fm, body=parsed.body)
    new_content = render_note(new_parsed)
    params_hash_value = params_hash(path, *params)

    # Validation runs against the desired post-write state, BEFORE we touch
    # disk and identically in dry-run vs real-write mode.
    if hooks is not None:
        try:
            run_validation_hooks(
                hooks,
                HookContext(
                    path=vp,
                    new_frontmatter=(
                        None if new_fm is None else to_plain_dict(dict(new_fm))
                    ),
                    new_body=parsed.body,
                    operation=tool_name,
                ),
            )
        except Exception as exc:
            return map_exception(exc)

    if dry_run:
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            op_kind="write",
            vault_path=str(vp.relative),
            outcome="success",
            started=started,
            params_hash=params_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                "path": str(vp.relative),
                "request_id": request_id,
                "new_content": new_content,
                "new_frontmatter": _to_json_safe(dict(new_fm)) if new_fm else None,
            },
            dry_run=True,
            audit_id=audit_id,
        )

    from obsidian_hardened_mcp.fs.writer import atomic_write_text

    try:
        atomic_write_text(vp, new_content)
    except Exception as exc:
        return map_exception(exc)

    audit_id = emit_audit(
        audit,
        request_id=request_id,
        tool=tool_name,
        op_kind="write",
        vault_path=str(vp.relative),
        outcome="success",
        started=started,
        params_hash=params_hash_value,
        dry_run=False,
    )
    return ToolResult(
        ok=True,
        data={
            "path": str(vp.relative),
            "request_id": request_id,
            "new_frontmatter": _to_json_safe(dict(new_fm)) if new_fm else None,
        },
        audit_id=audit_id,
    )


class _FieldNotFoundError(Exception):
    """Internal sentinel used by `_delete_field`."""


class _UnsafeValueError(ValueError):
    """Internal sentinel raised by `_ensure_safe_value`."""


_TAG_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


class _InvalidTagError(ValueError):
    """Internal sentinel raised by `_normalize_tag` and friends. Caller
    converts this to `ErrorCode.INVALID_TAG`."""


def _normalize_tag(raw: str) -> str:
    """Trim whitespace, strip a single leading '#', then validate.

    Raises `_InvalidTagError` if the result is empty, has invalid chars
    (anything outside [A-Za-z0-9_./-]), or starts/ends with '/'.
    """
    s = raw.strip()
    if s.startswith("#"):
        s = s[1:].strip()
    if not s:
        raise _InvalidTagError(
            f"tag {raw!r} invalid: empty after '#' strip and trim"
        )
    if not _TAG_RE.match(s):
        raise _InvalidTagError(
            f"tag {s!r} invalid: must match [A-Za-z0-9_./-]+"
        )
    if s.startswith("/") or s.endswith("/"):
        raise _InvalidTagError(
            f"tag {s!r} invalid: must not start or end with '/'"
        )
    return s


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


def _dedupe_in_order(items: Iterable[str]) -> list[str]:
    """Iterate over `items`, returning a new list with duplicates removed,
    preserving first-occurrence order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class _MalformedTagsFieldError(ValueError):
    """Raised when an existing 'tags:' field is not a list of strings."""


def _extract_existing_tags(fm: CommentedMap | None) -> list[str]:
    """Return existing tags as a `list[str]`, or `[]` if absent.

    Raises `_MalformedTagsFieldError` if `tags:` exists but is not a list
    of strings.
    """
    if fm is None or "tags" not in fm:
        return []
    raw = fm["tags"]
    if not isinstance(raw, list):
        raise _MalformedTagsFieldError(
            "existing 'tags:' field is not a list of strings"
        )
    if not all(isinstance(t, str) for t in raw):
        raise _MalformedTagsFieldError(
            "existing 'tags:' field is not a list of strings"
        )
    return list(raw)


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
    """Deep-merge `patch` into `target`.

    Behaviour on type mismatch (e.g. patch wants dict at `k` but target has
    a list/scalar/None there) is "wholesale replace at the offending key" —
    we never coerce or mix shapes.
    """
    for k, v in patch.items():
        existing = target.get(k)
        if isinstance(v, dict) and isinstance(existing, CommentedMap):
            _deep_merge_into(existing, v)
        else:
            target[k] = v


# ---------------------------------------------------------------------------
# Write-side value whitelist (closes the YAML safety loop with the parser)
# ---------------------------------------------------------------------------

_MAX_VALUE_DEPTH = 16
_MAX_STRING_LENGTH = 64 * 1024
_MAX_KEYS_PER_DICT = 1024
_MAX_LIST_ITEMS = 1024


def _ensure_safe_value(value: Any, *, _depth: int = 0) -> None:
    """Refuse any frontmatter value that isn't a JSON-compatible scalar /
    list / dict.

    Closes the loop with `frontmatter.parser._reject_custom_tags`: that
    function refuses arbitrary YAML tags coming IN; this one refuses values
    that would get tagged when going OUT (a `Path`, a `set`, a custom class,
    etc.). Without this, a client can polute the file with constructs the
    parser would later refuse to read back.

    Allowed types: None, bool, int, float, str, list[Allowed], dict[str, Allowed].
    Rejected: bytes, datetime/date objects (clients should pass ISO strings),
    Path, set/frozenset, tuple, custom classes, anything else.
    """
    if _depth > _MAX_VALUE_DEPTH:
        raise _UnsafeValueError(
            f"value nesting exceeds depth {_MAX_VALUE_DEPTH}"
        )
    if value is None or isinstance(value, bool):
        return
    # `bool` is a subclass of `int` — handled above first.
    if isinstance(value, (int, float)):
        return
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise _UnsafeValueError(
                f"string value exceeds {_MAX_STRING_LENGTH} chars"
            )
        return
    if isinstance(value, dict):
        if len(value) > _MAX_KEYS_PER_DICT:
            raise _UnsafeValueError(
                f"dict has more than {_MAX_KEYS_PER_DICT} keys"
            )
        for k, v in value.items():
            if not isinstance(k, str):
                raise _UnsafeValueError(
                    f"dict keys must be strings, got {type(k).__name__}"
                )
            _ensure_safe_value(v, _depth=_depth + 1)
        return
    if isinstance(value, list):
        if len(value) > _MAX_LIST_ITEMS:
            raise _UnsafeValueError(
                f"list has more than {_MAX_LIST_ITEMS} items"
            )
        for item in value:
            _ensure_safe_value(item, _depth=_depth + 1)
        return
    raise _UnsafeValueError(
        f"value type not allowed in frontmatter: {type(value).__name__}"
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
