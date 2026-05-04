"""Frontmatter parser.

Splits a Markdown note into a YAML frontmatter block and a body, parsing the
block with `ruamel.yaml` in round-trip mode so that comments, key ordering
and quote styles survive a write-back.

Safety:
    - Rejects YAML tags that the round-trip constructor cannot resolve
      (e.g. `!!python/object/apply`); raises `UnsafeYamlError`.
    - Caps the frontmatter block size to defeat decompression / billion-laughs
      style attacks; raises `FrontmatterTooLargeError`.
    - Refuses non-mapping top-level YAML (`MalformedFrontmatterError`).

Lenient on missing closing marker:
    A leading `---\\n` with no closing `---` line is treated as a plain
    Markdown horizontal rule — the file has no frontmatter. This matches
    Obsidian's behaviour and lets the server read existing files that the
    user never intended as having frontmatter. Use `parse_note(..., strict=True)`
    to demand a closing marker (raises `MalformedFrontmatterError`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.constructor import ConstructorError
from ruamel.yaml.error import YAMLError

from obsidian_power_mcp.frontmatter.yaml_safety import enforce_default_tags_only

DEFAULT_MAX_FRONTMATTER_BYTES = 64 * 1024

# Closing marker: a line that is exactly `---` (LF or CRLF), at start of file
# or preceded by a newline.
_CLOSING_MARKER = re.compile(r"(?:\A|\r?\n)---(?:\r?\n|\Z)")


class FrontmatterError(Exception):
    """Base for frontmatter parse/render errors."""


class MalformedFrontmatterError(FrontmatterError):
    """Frontmatter block is malformed (invalid YAML, non-mapping top level,
    or — in strict mode — missing closing marker)."""


class FrontmatterTooLargeError(FrontmatterError):
    """Frontmatter block exceeds the size budget."""


class UnsafeYamlError(FrontmatterError):
    """Frontmatter contains YAML constructs that the safe loader rejects
    (e.g. Python object tags)."""


@dataclass(frozen=True)
class ParsedNote:
    """Result of parsing a Markdown note.

    Attributes:
        frontmatter: a `CommentedMap` (round-trip aware) when a frontmatter
            block was present, otherwise `None`. An empty block (`---\\n---\\n`)
            yields an empty `CommentedMap` (`{}`-equivalent), distinct from
            "no block at all".
        body: everything that follows the closing `---` separator (or the
            full text when no block is present).
    """

    frontmatter: CommentedMap | None
    body: str


def _new_yaml_loader() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.allow_duplicate_keys = False
    return yaml


def parse_note(
    text: str,
    *,
    max_frontmatter_bytes: int = DEFAULT_MAX_FRONTMATTER_BYTES,
    strict: bool = False,
) -> ParsedNote:
    """Parse a Markdown text into a `ParsedNote`.

    Args:
        text: raw note content.
        max_frontmatter_bytes: cap on the YAML block in UTF-8 bytes. Must be
            tightly bounded — the default of 64 KiB is far above any
            legitimate vault frontmatter.
        strict: if True, raise `MalformedFrontmatterError` when a leading
            `---` opener has no matching closing marker. If False (default),
            treat such files as having no frontmatter — Obsidian-compatible.

    Raises:
        MalformedFrontmatterError, FrontmatterTooLargeError, UnsafeYamlError
    """
    if not text:
        return ParsedNote(frontmatter=None, body="")

    if text.startswith("---\r\n"):
        opening_len = 5
    elif text.startswith("---\n"):
        opening_len = 4
    else:
        return ParsedNote(frontmatter=None, body=text)

    rest = text[opening_len:]
    split = _split_at_closing_marker(rest)
    if split is None:
        if strict:
            raise MalformedFrontmatterError(
                "frontmatter block is not closed by a `---` line"
            )
        # Lenient: treat the whole file as plain markdown.
        return ParsedNote(frontmatter=None, body=text)

    yaml_block, body = split
    if len(yaml_block.encode("utf-8")) > max_frontmatter_bytes:
        raise FrontmatterTooLargeError(
            f"frontmatter block exceeds {max_frontmatter_bytes} bytes"
        )

    fm = _load_yaml_block(yaml_block)
    return ParsedNote(frontmatter=fm, body=body)


def _split_at_closing_marker(rest: str) -> tuple[str, str] | None:
    """Locate the closing `---` line and split the YAML block from the body.

    Returns `None` when no closing marker exists.
    """
    match = _CLOSING_MARKER.search(rest)
    if match is None:
        return None
    return rest[: match.start()], rest[match.end() :]


def _load_yaml_block(yaml_block: str) -> CommentedMap:
    yaml = _new_yaml_loader()
    if not yaml_block.strip():
        return CommentedMap()
    try:
        loaded = yaml.load(yaml_block + "\n")
    except ConstructorError as exc:
        # Round-trip constructor refuses some constructs outright.
        raise UnsafeYamlError(f"unsafe YAML construct: {exc.problem}") from exc
    except YAMLError as exc:
        raise MalformedFrontmatterError(f"invalid YAML: {exc}") from exc
    if loaded is None:
        return CommentedMap()
    if not isinstance(loaded, CommentedMap):
        raise MalformedFrontmatterError(
            "frontmatter must be a YAML mapping at the top level"
        )
    enforce_default_tags_only(loaded, error_class=UnsafeYamlError)
    return loaded


def render_note(parsed: ParsedNote) -> str:
    """Serialise a `ParsedNote` back to a Markdown string.

    When `parsed.frontmatter` is `None`, no block is emitted — only `body`
    is returned. An empty mapping yields `---\\n---\\n` followed by `body`.
    Round-trip details (comments, quote style, key order) are preserved
    when the input came from `parse_note`.
    """
    if parsed.frontmatter is None:
        return parsed.body
    if len(parsed.frontmatter) == 0:
        return f"---\n---\n{parsed.body}"

    yaml = _new_yaml_loader()
    buf = StringIO()
    yaml.dump(parsed.frontmatter, buf)
    yaml_text = buf.getvalue()
    if not yaml_text.endswith("\n"):  # pragma: no cover - ruamel always appends \n
        yaml_text += "\n"
    return f"---\n{yaml_text}---\n{parsed.body}"
