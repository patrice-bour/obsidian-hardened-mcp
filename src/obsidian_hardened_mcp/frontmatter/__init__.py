# SPDX-License-Identifier: Apache-2.0
"""Frontmatter parsing and serialisation.

Public API:
    parse_note(text)        -> ParsedNote
    render_note(parsed)     -> str
    ParsedNote              dataclass holding (frontmatter, body)
    FrontmatterError        base exception
    MalformedFrontmatterError, FrontmatterTooLargeError, UnsafeYamlError
"""

from __future__ import annotations

from obsidian_hardened_mcp.frontmatter.parser import (
    FrontmatterError,
    FrontmatterTooLargeError,
    MalformedFrontmatterError,
    ParsedNote,
    UnsafeYamlError,
    parse_note,
    render_note,
)

__all__ = [
    "FrontmatterError",
    "FrontmatterTooLargeError",
    "MalformedFrontmatterError",
    "ParsedNote",
    "UnsafeYamlError",
    "parse_note",
    "render_note",
]
