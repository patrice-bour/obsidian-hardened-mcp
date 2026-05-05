"""Unit tests for tools._base — exception mapping."""

from __future__ import annotations

import pytest

from obsidian_hardened_mcp.domain.results import ErrorCode, ToolResult
from obsidian_hardened_mcp.domain.vault_path import (
    AbsolutePathError,
    ForbiddenZoneError,
    InvalidPathError,
    PathEscapeError,
    SymlinkEscapeError,
)
from obsidian_hardened_mcp.fs.reader import (
    FileOffloadedError,
    FileTooLargeError,
    NotAFileError,
    NotFoundError,
)
from obsidian_hardened_mcp.tools._base import map_exception, tool_call


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (AbsolutePathError("x"), ErrorCode.ABSOLUTE_PATH),
        (PathEscapeError("x"), ErrorCode.PATH_ESCAPE),
        (SymlinkEscapeError("x"), ErrorCode.SYMLINK_ESCAPE),
        (ForbiddenZoneError("x"), ErrorCode.FORBIDDEN_ZONE),
        (InvalidPathError("x"), ErrorCode.INVALID_PATH),
        (NotFoundError("x"), ErrorCode.NOT_FOUND),
        (NotAFileError("x"), ErrorCode.NOT_A_FILE),
        (FileTooLargeError("x"), ErrorCode.FILE_TOO_LARGE),
        (FileOffloadedError("x"), ErrorCode.FILE_OFFLOADED),
        (PermissionError("x"), ErrorCode.PERMISSION_DENIED),
        (RuntimeError("x"), ErrorCode.INTERNAL_ERROR),
    ],
)
def test_map_exception_dispatches(exc: Exception, expected: ErrorCode) -> None:
    result = map_exception(exc)
    assert not result.ok
    assert result.error is not None
    assert result.error.code is expected


def test_tool_call_decorator_wraps_exceptions() -> None:
    @tool_call
    def failing() -> ToolResult:
        raise ForbiddenZoneError(".git")

    result = failing()
    assert not result.ok
    assert result.error is not None
    assert result.error.code is ErrorCode.FORBIDDEN_ZONE


def test_tool_call_decorator_passes_through_success() -> None:
    @tool_call
    def ok() -> ToolResult:
        return ToolResult.success(data={"x": 1})

    result = ok()
    assert result.ok
    assert result.data == {"x": 1}
