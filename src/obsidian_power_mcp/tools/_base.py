"""Shared helpers for tool implementations.

Maps internal exceptions to public `ErrorCode`s and provides a
`tool_call` decorator that wraps any exception into a `ToolResult.failure`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from obsidian_power_mcp.domain.results import ErrorCode, ToolResult
from obsidian_power_mcp.domain.vault_path import (
    AbsolutePathError,
    ForbiddenZoneError,
    InvalidPathError,
    PathEscapeError,
    SymlinkEscapeError,
    VaultPathError,
)
from obsidian_power_mcp.fs.reader import (
    FileOffloadedError,
    FileTooLargeError,
    NotAFileError,
    NotFoundError,
)

P = ParamSpec("P")
R = TypeVar("R", bound=ToolResult)


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
