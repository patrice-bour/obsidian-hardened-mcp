"""Tool result and error types.

Tools return `ToolResult` objects. The MCP layer serialises these to JSON.
Internal exceptions are mapped to `ErrorCode` values; tool implementations
should never let raw `VaultPathError` (or other exceptions) bubble up to MCP.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes for MCP clients."""

    INVALID_PATH = "invalid_path"
    PATH_ESCAPE = "path_escape"
    SYMLINK_ESCAPE = "symlink_escape"
    FORBIDDEN_ZONE = "forbidden_zone"
    ABSOLUTE_PATH = "absolute_path"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    PERMISSION_DENIED = "permission_denied"
    FILE_TOO_LARGE = "file_too_large"
    FILE_OFFLOADED = "file_offloaded"
    NOT_A_FILE = "not_a_file"
    NOT_A_DIRECTORY = "not_a_directory"
    UNSAFE_YAML = "unsafe_yaml"
    MALFORMED_FRONTMATTER = "malformed_frontmatter"
    FRONTMATTER_TOO_LARGE = "frontmatter_too_large"
    INTERNAL_ERROR = "internal_error"


class ErrorInfo(BaseModel):
    """Structured error payload."""

    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class ToolResult(BaseModel):
    """Uniform result envelope for every MCP tool."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    data: dict[str, Any] | None = None
    error: ErrorInfo | None = None
    dry_run: bool = False
    audit_id: str | None = Field(
        default=None,
        description="Audit log identifier for write/destructive operations.",
    )

    @classmethod
    def success(
        cls, data: dict[str, Any] | None = None, *, dry_run: bool = False
    ) -> ToolResult:
        return cls(ok=True, data=data, dry_run=dry_run)

    @classmethod
    def failure(
        cls,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(ok=False, error=ErrorInfo(code=code, message=message, details=details))
