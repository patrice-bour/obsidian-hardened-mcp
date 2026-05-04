"""Wikilink resolution.

`resolve_wikilink` parses an Obsidian-style `[[...]]` link content (without
the brackets — but full bracket form is also accepted as a courtesy) and
returns the path of the note it resolves to, plus any heading / block id /
display alias.

Resolution rules (Obsidian-compatible):

1. If the target contains a slash, treat it as a vault-relative path. The
   path goes through `VaultPath.from_user`, so traversal/sandbox checks
   apply. `.md` extension is appended if missing.
2. Otherwise it's a basename lookup against every markdown file in the
   vault (forbidden zones pruned). Multiple matches → `ambiguous=True`,
   `candidates` listed.
3. When `from_path` is provided AND the basename is ambiguous, prefer the
   candidate whose folder is closest to `from_path` (Obsidian's
   shortest-relative resolution).

Output shape:

```python
{
    "target": "Alpha#Section|Display",   # echoed input minus brackets
    "resolved": "01_Notes/Alpha.md" | None,
    "alias": "Display" | None,
    "heading": "Section" | None,
    "block_id": "abc123" | None,         # exclusive with `heading`
    "ambiguous": False,
    "candidates": ["01_Notes/Alpha.md"], # always set, even on a unique hit
}
```
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode, ToolResult
from obsidian_power_mcp.domain.vault_path import VaultPath
from obsidian_power_mcp.fs.listing import iter_markdown
from obsidian_power_mcp.tools._base import map_exception


@dataclass(frozen=True)
class _ParsedLink:
    target: str
    alias: str | None
    heading: str | None
    block_id: str | None


def resolve_wikilink(
    config: AppConfig,
    target: str,
    *,
    from_path: str | None = None,
) -> ToolResult:
    """Resolve a wikilink target to a vault-relative `.md` path."""
    # Windows clients sometimes paste `\\`; normalise so the path-form
    # detection below works uniformly. Real backslashes in note names are
    # vanishingly rare in Obsidian and not worth a separate code path.
    target = target.replace("\\", "/")

    try:
        raw = _strip_brackets(target).strip()
    except ValueError as exc:
        return ToolResult.failure(ErrorCode.INVALID_PATH, str(exc))
    if not raw:
        return ToolResult.failure(
            ErrorCode.INVALID_PATH, "wikilink target must not be empty"
        )

    parsed = _parse_link_syntax(raw)
    if not parsed.target:
        return ToolResult.failure(
            ErrorCode.INVALID_PATH, f"wikilink {target!r} has no target name"
        )

    if "/" in parsed.target:
        try:
            resolved = _resolve_path_form(config, parsed.target)
        except Exception as exc:
            return map_exception(exc)
        candidates = [resolved] if resolved is not None else []
        return _build_result(
            target=raw,
            parsed=parsed,
            resolved=resolved,
            ambiguous=False,
            candidates=candidates,
        )

    candidates = _candidates_by_basename(config, parsed.target)
    if len(candidates) == 0:
        return _build_result(
            target=raw,
            parsed=parsed,
            resolved=None,
            ambiguous=False,
            candidates=[],
        )
    if len(candidates) == 1:
        return _build_result(
            target=raw,
            parsed=parsed,
            resolved=candidates[0],
            ambiguous=False,
            candidates=candidates,
        )

    if from_path is not None:
        chosen = _shortest_relative(candidates, from_path)
        if chosen is not None:
            return _build_result(
                target=raw,
                parsed=parsed,
                resolved=chosen,
                ambiguous=False,
                candidates=candidates,
            )
    return _build_result(
        target=raw,
        parsed=parsed,
        resolved=None,
        ambiguous=True,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _strip_brackets(raw: str) -> str:
    s = raw.strip()
    starts = s.startswith("[[")
    ends = s.endswith("]]")
    if starts and ends:
        return s[2:-2]
    if starts or ends:
        raise ValueError(
            f"mismatched `[[]]` brackets in wikilink target {raw!r}"
        )
    return s


def _parse_link_syntax(raw: str) -> _ParsedLink:
    """Split `Target#Heading|Alias` (or `Target#^block-id|Alias`) into parts."""
    alias: str | None = None
    heading: str | None = None
    block_id: str | None = None

    body = raw
    if "|" in body:
        body, alias = body.split("|", 1)
        alias = alias.strip() or None

    if "#" in body:
        body, anchor = body.split("#", 1)
        if anchor.startswith("^"):
            block_id = anchor[1:].strip() or None
        else:
            heading = anchor.strip() or None

    return _ParsedLink(
        target=body.strip(),
        alias=alias,
        heading=heading,
        block_id=block_id,
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve_path_form(config: AppConfig, target: str) -> str | None:
    candidate = target if target.endswith(".md") else f"{target}.md"
    vp = VaultPath.from_user(candidate, config.vault_root)
    return str(vp.relative) if vp.absolute.exists() else None


def _candidates_by_basename(config: AppConfig, target: str) -> list[str]:
    name = target if target.endswith(".md") else f"{target}.md"
    name_lower = name.lower()
    out: list[str] = []
    for md_path in iter_markdown(config.vault_root):
        if md_path.name.lower() == name_lower:
            out.append(md_path.relative_to(config.vault_root).as_posix())
    out.sort()
    return out


def _shortest_relative(candidates: list[str], from_path: str) -> str | None:
    """Pick the candidate whose folder shares the longest prefix with
    `from_path`. Used to disambiguate notes with the same basename."""
    from_dir = str(PurePosixPath(from_path).parent)
    if from_dir == ".":
        return None
    best: tuple[int, str] | None = None
    for cand in candidates:
        cand_dir = str(PurePosixPath(cand).parent)
        score = _common_prefix_segments(from_dir, cand_dir)
        if best is None or score > best[0]:
            best = (score, cand)
    if best is None:
        return None  # pragma: no cover - candidates non-empty by caller
    score, chosen = best
    return chosen if score > 0 else None


def _common_prefix_segments(a: str, b: str) -> int:
    a_parts = a.split("/")
    b_parts = b.split("/")
    n = 0
    for x, y in zip(a_parts, b_parts, strict=False):
        if x == y:
            n += 1
        else:
            break
    return n


# ---------------------------------------------------------------------------
# Result building
# ---------------------------------------------------------------------------


def _build_result(
    *,
    target: str,
    parsed: _ParsedLink,
    resolved: str | None,
    ambiguous: bool,
    candidates: list[str],
) -> ToolResult:
    data: dict[str, Any] = {
        "target": target,
        "resolved": resolved,
        "alias": parsed.alias,
        "heading": parsed.heading,
        "block_id": parsed.block_id,
        "ambiguous": ambiguous,
        "candidates": candidates,
    }
    return ToolResult.success(data=data)
