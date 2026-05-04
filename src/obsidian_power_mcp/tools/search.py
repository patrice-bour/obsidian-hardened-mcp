"""Note search.

`search_notes` walks the vault (with forbidden-zone pruning), parses each
candidate's frontmatter, and matches against a literal query. Three modes:

- `fulltext`: the body must contain the query (case-insensitive).
- `frontmatter`: a frontmatter field value (recursive into lists/dicts of
  strings) must contain the query.
- `combined` (default): either of the above counts as a match.

Filters: `folder` restricts the scan root; `tag` requires the value to
appear in the note's `tags:` list; `type_filter` requires `type:` to
match exactly.

Engine: pure Python in v0.1 — adequate for vaults up to a few thousand
notes. A ripgrep-backed engine is on the v0.2 roadmap (see
`docs/v0.1-followups.md`).
"""

from __future__ import annotations

from typing import Any, Literal

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode, ToolResult
from obsidian_power_mcp.domain.vault_path import VaultPath
from obsidian_power_mcp.frontmatter import parse_note
from obsidian_power_mcp.fs.listing import iter_markdown
from obsidian_power_mcp.fs.reader import read_text
from obsidian_power_mcp.tools._base import map_exception, to_plain_dict

SearchMode = Literal["fulltext", "frontmatter", "combined"]
_VALID_MODES: frozenset[str] = frozenset({"fulltext", "frontmatter", "combined"})

_SNIPPET_MAX_BYTES = 200


def search_notes(
    config: AppConfig,
    query: str,
    *,
    mode: SearchMode = "combined",
    folder: str | None = None,
    tag: str | None = None,
    type_filter: str | None = None,
    limit: int = 50,
) -> ToolResult:
    """Search markdown notes.

    Returns matches with a snippet, a `match_kind` (`fulltext` /
    `frontmatter`), and the path. Truncated when more matches than `limit`.
    """
    if not query:
        return ToolResult.failure(
            ErrorCode.INVALID_PATH, "query string must not be empty"
        )
    if mode not in _VALID_MODES:
        return ToolResult.failure(
            ErrorCode.INVALID_PATH,
            f"unknown mode {mode!r}; expected one of {sorted(_VALID_MODES)}",
        )

    if folder is None:
        scan_root = config.vault_root
    else:
        try:
            vp = VaultPath.from_user(folder, config.vault_root)
        except Exception as exc:
            return map_exception(exc)
        scan_root = vp.absolute

    if limit <= 0 or limit > config.max_batch:
        limit = min(max(limit, 1), config.max_batch)

    matches: list[dict[str, Any]] = []
    truncated = False
    skipped_read = 0
    skipped_parse = 0
    q_lower = query.lower()

    for md_path in iter_markdown(scan_root):
        try:
            text = read_text(
                md_path_to_vault_path(md_path, config),
                max_size_bytes=config.max_file_size_bytes,
            )
        except Exception:
            # unreadable file (oversized / offloaded / permission /
            # symlink-swap during walk) MUST NOT abort the whole search,
            # but the client deserves to know how many we dropped.
            skipped_read += 1
            continue

        try:
            parsed = parse_note(text)
        except Exception:
            # malformed-frontmatter: don't kill the search, surface the
            # signal to the caller via `skipped_parse`.
            skipped_parse += 1
            continue

        fm_plain = (
            None
            if parsed.frontmatter is None
            else to_plain_dict(dict(parsed.frontmatter))
        )

        if not _passes_filters(fm_plain, tag=tag, type_filter=type_filter):
            continue

        match = _match_note(parsed.body, fm_plain, q_lower, mode)
        if match is None:
            continue

        rel = md_path.relative_to(config.vault_root).as_posix()
        matches.append({"path": rel, **match})

        if len(matches) > limit:
            truncated = True
            matches = matches[:limit]
            break

    matches.sort(key=lambda m: m["path"])

    data: dict[str, Any] = {
        "query": query,
        "mode": mode,
        "matches": matches,
        "truncated": truncated,
        "limit": limit,
        "engine": "python",
        "skipped_read": skipped_read,
        "skipped_parse": skipped_parse,
    }
    return ToolResult.success(data=data)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def md_path_to_vault_path(md_path, config: AppConfig) -> VaultPath:  # type: ignore[no-untyped-def]
    """Convert a `Path` produced by `iter_markdown` (which already pruned
    forbidden zones) into a `VaultPath`. The path is known-good — we only
    use the constructor's normalisation, not the safety checks."""
    rel = md_path.relative_to(config.vault_root).as_posix()
    return VaultPath.from_user(rel, config.vault_root)


def _passes_filters(
    fm: dict[str, Any] | None,
    *,
    tag: str | None,
    type_filter: str | None,
) -> bool:
    if tag is not None:
        if fm is None:
            return False
        tags = fm.get("tags")
        if not isinstance(tags, list) or tag not in tags:
            return False
    if type_filter is not None:
        if fm is None:
            return False
        if fm.get("type") != type_filter:
            return False
    return True


def _match_note(
    body: str,
    fm: dict[str, Any] | None,
    q_lower: str,
    mode: SearchMode,
) -> dict[str, Any] | None:
    fulltext_snippet: str | None = None
    fm_match: tuple[str, str] | None = None

    if mode in ("fulltext", "combined"):
        fulltext_snippet = _match_fulltext(body, q_lower)
    if mode in ("frontmatter", "combined"):
        fm_match = _match_frontmatter(fm, q_lower)

    if mode == "fulltext":
        if fulltext_snippet is None:
            return None
        return {"match_kind": "fulltext", "snippet": fulltext_snippet}

    if mode == "frontmatter":
        if fm_match is None:
            return None
        key, snippet = fm_match
        return {"match_kind": "frontmatter", "field": key, "snippet": snippet}

    # combined: report BOTH when both hit, so the client never loses a signal.
    if fulltext_snippet is not None and fm_match is not None:
        key, fm_snippet = fm_match
        return {
            "match_kind": "combined",
            "snippet": fulltext_snippet,
            "frontmatter_field": key,
            "frontmatter_snippet": fm_snippet,
        }
    if fulltext_snippet is not None:
        return {"match_kind": "fulltext", "snippet": fulltext_snippet}
    if fm_match is not None:
        key, snippet = fm_match
        return {"match_kind": "frontmatter", "field": key, "snippet": snippet}
    return None


def _match_fulltext(body: str, q_lower: str) -> str | None:
    for line in body.splitlines():
        if q_lower in line.lower():
            return line.strip()[:_SNIPPET_MAX_BYTES]
    return None


def _match_frontmatter(
    fm: dict[str, Any] | None, q_lower: str
) -> tuple[str, str] | None:
    if fm is None:
        return None
    return _walk_for_match(fm, q_lower, path="")


def _walk_for_match(
    node: Any, q_lower: str, *, path: str
) -> tuple[str, str] | None:
    if isinstance(node, str):
        if q_lower in node.lower():
            return path or "<root>", node[:_SNIPPET_MAX_BYTES]
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            sub_path = f"{path}.{k}" if path else str(k)
            result = _walk_for_match(v, q_lower, path=sub_path)
            if result is not None:
                return result
    elif isinstance(node, list):
        for i, item in enumerate(node):
            result = _walk_for_match(item, q_lower, path=f"{path}[{i}]")
            if result is not None:
                return result
    return None
