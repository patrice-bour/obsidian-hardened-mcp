# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for tool implementations.

- `map_exception` / `tool_call`: turn internal exceptions into public
  `ErrorCode`s without leaking stack traces.
- `new_request_id`: per-tool-call unique identifier; generated ONCE at the
  tool boundary and propagated through every `emit_audit` call.
- `params_hash`: canonical JSON-based fingerprint of a tool's input
  parameters (stable across Python versions and dict insertion orders).
- `emit_audit`: pyramidalises the audit-event construction so write tools
  do not import logger plumbing themselves.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from obsidian_hardened_mcp.domain.audit import AuditEvent, OpKind, Outcome
from obsidian_hardened_mcp.domain.results import ErrorCode, ToolResult
from obsidian_hardened_mcp.domain.vault_path import (
    AbsolutePathError,
    ForbiddenZoneError,
    InvalidPathError,
    PathEscapeError,
    SymlinkEscapeError,
    VaultPathError,
)
from obsidian_hardened_mcp.frontmatter import (
    FrontmatterTooLargeError,
    MalformedFrontmatterError,
    UnsafeYamlError,
)
from obsidian_hardened_mcp.fs.reader import (
    FileOffloadedError,
    FileTooLargeError,
    NotAFileError,
    NotFoundError,
)
from obsidian_hardened_mcp.rest.client import (
    RestAuthError,
    RestError,
    RestUnavailableError,
)
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.security.confirm import (
    ExpiredConfirmationTokenError,
    InvalidConfirmationTokenError,
    PayloadMismatchError,
)
from obsidian_hardened_mcp.validation.hooks import (
    HookContext,
    HookRegistry,
    HookViolationError,
)

P = ParamSpec("P")
R = TypeVar("R", bound=ToolResult)


# ---------------------------------------------------------------------------
# Exception -> ErrorCode mapping
# ---------------------------------------------------------------------------


def map_exception(exc: Exception) -> ToolResult:
    """Translate an internal exception into a `ToolResult.failure`.

    Add new branches here as new exception types appear in the codebase.
    Unknown exceptions become `ErrorCode.INTERNAL_ERROR` so they are still
    visible to the client without leaking stack traces.
    """
    match exc:
        case AbsolutePathError():
            code = ErrorCode.ABSOLUTE_PATH
        case PathEscapeError():
            code = ErrorCode.PATH_ESCAPE
        case SymlinkEscapeError():
            code = ErrorCode.SYMLINK_ESCAPE
        case ForbiddenZoneError():
            code = ErrorCode.FORBIDDEN_ZONE
        case InvalidPathError():
            code = ErrorCode.INVALID_PATH
        case VaultPathError():  # pragma: no cover - sealed hierarchy
            code = ErrorCode.INVALID_PATH
        case NotFoundError():
            code = ErrorCode.NOT_FOUND
        case NotAFileError():
            code = ErrorCode.NOT_A_FILE
        case FileTooLargeError():
            code = ErrorCode.FILE_TOO_LARGE
        case FileOffloadedError():
            code = ErrorCode.FILE_OFFLOADED
        case UnsafeYamlError():
            code = ErrorCode.UNSAFE_YAML
        case MalformedFrontmatterError():
            code = ErrorCode.MALFORMED_FRONTMATTER
        case FrontmatterTooLargeError():
            code = ErrorCode.FRONTMATTER_TOO_LARGE
        case HookViolationError():
            code = ErrorCode.VALIDATION_FAILED
        case ExpiredConfirmationTokenError():
            code = ErrorCode.EXPIRED_CONFIRMATION_TOKEN
        case PayloadMismatchError():
            code = ErrorCode.PAYLOAD_MISMATCH
        case InvalidConfirmationTokenError():
            code = ErrorCode.INVALID_CONFIRMATION_TOKEN
        case RestAuthError():
            code = ErrorCode.REST_AUTH_FAILED
        case RestUnavailableError():
            code = ErrorCode.REST_UNAVAILABLE
        case RestError():
            code = ErrorCode.REST_ERROR
        case PermissionError():
            code = ErrorCode.PERMISSION_DENIED
        case _:
            code = ErrorCode.INTERNAL_ERROR
    return ToolResult.failure(code, str(exc))


def tool_call(func: Callable[P, ToolResult]) -> Callable[P, ToolResult]:
    """Decorator: wrap a tool function so any uncaught exception becomes
    a `ToolResult.failure(...)` rather than escaping to the MCP layer."""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> ToolResult:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            return map_exception(exc)

    return wrapper


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def new_request_id() -> str:
    """Generate a fresh request id. Call ONCE per tool invocation; pass the
    result into every `emit_audit` made by that invocation so the audit
    trail can correlate them.
    """
    return secrets.token_hex(8)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def params_hash(*parts: object) -> str:
    """Canonical fingerprint of tool parameters.

    Uses `json.dumps(..., sort_keys=True)` so dicts with the same keys but
    different insertion order produce the same hash. Non-JSON values fall
    back to `str(v)` via `default=`. Returns the leading 16 hex chars of
    sha256 — enough entropy for replay/dedup without bloating the audit log.
    """

    def _safe_default(value: Any) -> str:
        return repr(value)

    canonical = json.dumps(
        list(parts),
        sort_keys=True,
        separators=(",", ":"),
        default=_safe_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def emit_audit(
    audit: AuditLogger,
    *,
    request_id: str,
    tool: str,
    op_kind: OpKind,
    vault_path: str,
    outcome: Outcome,
    started: float,
    params_hash: str,
    dry_run: bool,
    snapshot_id: str | None = None,
) -> str:
    """Build an `AuditEvent` from the live state and append it to the log.

    `started` is a `time.monotonic()` reading taken at the start of the
    tool call; we compute `duration_ms` here so callers don't have to.
    """
    return audit.log(
        AuditEvent(
            ts=now_utc(),
            request_id=request_id,
            tool=tool,
            vault_path=vault_path,
            op_kind=op_kind,
            outcome=outcome,
            duration_ms=int((time.monotonic() - started) * 1000),
            params_hash=params_hash,
            dry_run=dry_run,
            snapshot_id=snapshot_id,
        )
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def run_validation_hooks(
    hooks: HookRegistry | None,
    ctx: HookContext,
) -> None:
    """Run the configured pre-write hooks against `ctx`. Raises
    `HookViolationError` on rejection (which `map_exception` then turns into
    `ErrorCode.VALIDATION_FAILED`). When `hooks` is None or empty, this is a
    no-op — the caller never has to check.
    """
    if hooks is None:
        return
    report = hooks.run(ctx)
    report.raise_for_rejection()


def to_plain_dict(value: Any) -> Any:
    """Recursively convert a ruamel `CommentedMap`/`CommentedSeq` tree to a
    plain `dict`/`list` of JSON-clean Python types — what hooks see in
    `HookContext`.

    `datetime.date` / `datetime.datetime` are emitted as ISO-8601 strings
    so hooks see exactly the same shape `get_frontmatter` would return to
    the MCP client. This means an `IsoDateHook` validating `date:` does
    not have to special-case "ruamel parsed it as a `date` object".
    """
    import datetime as _dt

    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_plain_dict(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain_dict(item) for item in value]
    return value
