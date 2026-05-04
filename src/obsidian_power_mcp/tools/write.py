"""Write tools — `create_note`, `update_note`, `append_to_note`, `patch_note`.

Every write op:
    1. Validates the path via `VaultPath.from_user`.
    2. Reads the existing file (when applicable) for the diff/preview.
    3. Performs the work atomically via `fs.writer.atomic_write_text`.
    4. Emits an `AuditEvent` to the JSONL log and returns its `audit_id`.

`dry_run=True` short-circuits step 3 and returns a preview of what would
have been written, but still emits the audit event with `dry_run=True`.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.audit import AuditEvent, OpKind, Outcome
from obsidian_power_mcp.domain.results import ErrorCode, ToolResult
from obsidian_power_mcp.domain.vault_path import VaultPath
from obsidian_power_mcp.fs.reader import read_text
from obsidian_power_mcp.fs.writer import AlreadyExistsError, atomic_write_text
from obsidian_power_mcp.security.audit_logger import AuditLogger
from obsidian_power_mcp.tools._base import map_exception


def _params_hash(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _emit(
    audit: AuditLogger,
    *,
    tool: str,
    op_kind: OpKind,
    vault_path: str,
    outcome: Outcome,
    started: float,
    params_hash: str,
    dry_run: bool,
) -> str:
    return audit.log(
        AuditEvent(
            ts=_now(),
            request_id=secrets.token_hex(8),
            tool=tool,
            vault_path=vault_path,
            op_kind=op_kind,
            outcome=outcome,
            duration_ms=int((time.monotonic() - started) * 1000),
            params_hash=params_hash,
            dry_run=dry_run,
        )
    )


def _execute_write(
    audit: AuditLogger,
    *,
    tool_name: str,
    vp: VaultPath,
    new_content: str,
    dry_run: bool,
    op_kind: OpKind = "write",
    exclusive: bool = False,
    extra_data: dict[str, Any] | None = None,
    started: float,
    params_hash: str,
) -> ToolResult:
    """Common tail for write tools: dry-run path + write + audit."""
    data: dict[str, Any] = {
        "path": str(vp.relative),
        "size": len(new_content.encode("utf-8")),
    }
    if extra_data:
        data.update(extra_data)

    if dry_run:
        data["new_content"] = new_content
        audit_id = _emit(
            audit,
            tool=tool_name,
            op_kind=op_kind,
            vault_path=str(vp.relative),
            outcome="success",
            started=started,
            params_hash=params_hash,
            dry_run=True,
        )
        return ToolResult(ok=True, data=data, dry_run=True, audit_id=audit_id)

    try:
        atomic_write_text(vp, new_content, exclusive=exclusive)
    except AlreadyExistsError as exc:
        audit_id = _emit(
            audit,
            tool=tool_name,
            op_kind=op_kind,
            vault_path=str(vp.relative),
            outcome="failure",
            started=started,
            params_hash=params_hash,
            dry_run=False,
        )
        result = ToolResult.failure(ErrorCode.ALREADY_EXISTS, str(exc))
        return result.model_copy(update={"audit_id": audit_id})

    audit_id = _emit(
        audit,
        tool=tool_name,
        op_kind=op_kind,
        vault_path=str(vp.relative),
        outcome="success",
        started=started,
        params_hash=params_hash,
        dry_run=False,
    )
    return ToolResult(ok=True, data=data, audit_id=audit_id)


def create_note(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    content: str,
    *,
    dry_run: bool = False,
) -> ToolResult:
    """Create a new note. Fails if the target already exists."""
    started = time.monotonic()
    try:
        vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)
    return _execute_write(
        audit,
        tool_name="create_note",
        vp=vp,
        new_content=content,
        dry_run=dry_run,
        exclusive=True,
        started=started,
        params_hash=_params_hash(path, len(content)),
    )


def update_note(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    content: str,
    *,
    dry_run: bool = False,
) -> ToolResult:
    """Replace a note's full content. Fails if the file does not exist."""
    started = time.monotonic()
    try:
        vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)
    if not vp.absolute.exists():
        return ToolResult.failure(
            ErrorCode.NOT_FOUND, f"file not found: {vp.relative}"
        )
    return _execute_write(
        audit,
        tool_name="update_note",
        vp=vp,
        new_content=content,
        dry_run=dry_run,
        started=started,
        params_hash=_params_hash(path, len(content)),
    )


def append_to_note(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    content: str,
    *,
    ensure_newline: bool = True,
    dry_run: bool = False,
) -> ToolResult:
    """Append text to an existing note (with optional separating newline)."""
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
    except Exception as exc:
        return map_exception(exc)

    if ensure_newline and existing and not existing.endswith("\n"):
        new_content = existing + "\n" + content
    else:
        new_content = existing + content

    return _execute_write(
        audit,
        tool_name="append_to_note",
        vp=vp,
        new_content=new_content,
        dry_run=dry_run,
        started=started,
        params_hash=_params_hash(path, len(content), ensure_newline),
    )


def patch_note(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    find: str,
    replace: str,
    *,
    count: int = 1,
    dry_run: bool = False,
) -> ToolResult:
    """Literal find-replace on a note.

    Args:
        find: literal text to look for (no regex).
        replace: replacement text.
        count: number of occurrences to replace. `0` means "all". Any other
            positive integer is the EXACT number expected; if the file
            contains more or fewer matches the operation aborts with an
            error and does NOT touch the file.
    """
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
    except Exception as exc:
        return map_exception(exc)

    occurrences = existing.count(find)
    if count > 0 and occurrences != count:
        return ToolResult.failure(
            ErrorCode.PATCH_COUNT_MISMATCH,
            f"expected exactly {count} occurrence(s) of {find!r}, found {occurrences}",
        )

    if count == 0:
        new_content = existing.replace(find, replace)
    else:
        new_content = existing.replace(find, replace, count)

    return _execute_write(
        audit,
        tool_name="patch_note",
        vp=vp,
        new_content=new_content,
        dry_run=dry_run,
        extra_data={"replacements": count if count > 0 else occurrences},
        started=started,
        params_hash=_params_hash(path, find, replace, count),
    )
