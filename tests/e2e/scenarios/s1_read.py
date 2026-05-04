"""S1 — read: list_notes, read_note, get_frontmatter, search_notes
(combined / fulltext / frontmatter), resolve_wikilink."""

from __future__ import annotations

from mcp_harness import E2EHarness

from ._assert import ScenarioReport, expect_ok, field_value


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S1", "read")

    # list_notes — expect 10 vault-relative paths
    listing = await h.call("list_notes")
    ok, why = expect_ok(listing, where="list_notes")
    rep.add("list_notes ok", ok, why)
    if ok:
        # data.notes is a list of vault-relative posix path strings.
        paths = set(field_value(listing, "notes") or [])
        rep.add(
            "list_notes returns 10 entries",
            len(paths) == 10,
            f"got {len(paths)}: {sorted(paths)[:5]}...",
        )
        for expected in ("index.md", "notes/alpha.md", "org/acme.md"):
            rep.add(
                f"listing contains {expected}",
                expected in paths,
                f"missing {expected}",
            )

    # read_note — alpha.md should contain the keyword
    alpha = await h.call("read_note", path="notes/alpha.md")
    ok, why = expect_ok(alpha, where="read_note alpha")
    rep.add("read_note alpha.md ok", ok, why)
    if ok:
        body = field_value(alpha, "content") or ""
        rep.add(
            "read_note returns alpha body",
            "needle-foo" in body and "[[beta]]" in body,
            f"body[:120]={body[:120]!r}",
        )

    # get_frontmatter — alpha.md fm has type=note + tags
    fm = await h.call("get_frontmatter", path="notes/alpha.md")
    ok, why = expect_ok(fm, where="get_frontmatter alpha")
    rep.add("get_frontmatter alpha ok", ok, why)
    if ok:
        front = field_value(fm, "frontmatter") or {}
        rep.add(
            "frontmatter type=note",
            front.get("type") == "note",
            f"got type={front.get('type')!r}",
        )
        rep.add(
            "frontmatter tags include foo",
            "foo" in (front.get("tags") or []),
            f"got tags={front.get('tags')!r}",
        )

    # search_notes — combined (default), keyword 'needle-foo'
    combined = await h.call("search_notes", query="needle-foo")
    ok, why = expect_ok(combined, where="search_notes combined")
    rep.add("search combined ok", ok, why)
    if ok:
        hits = _extract_paths(combined)
        rep.add(
            "search 'needle-foo' (combined) hits alpha",
            "notes/alpha.md" in hits,
            f"hits={hits}",
        )

    # search_notes — frontmatter mode, query="organisation"
    fm_search = await h.call(
        "search_notes", query="organisation", mode="frontmatter"
    )
    ok, why = expect_ok(fm_search, where="search_notes frontmatter")
    rep.add("search frontmatter ok", ok, why)
    if ok:
        hits = _extract_paths(fm_search)
        rep.add(
            "search 'organisation' (frontmatter) hits org/acme.md",
            "org/acme.md" in hits,
            f"hits={hits}",
        )

    # search_notes — fulltext mode, query="needle-bar" (only beta has it)
    ft = await h.call("search_notes", query="needle-bar", mode="fulltext")
    ok, why = expect_ok(ft, where="search_notes fulltext")
    rep.add("search fulltext ok", ok, why)
    if ok:
        hits = _extract_paths(ft)
        rep.add(
            "search 'needle-bar' (fulltext) returns only beta",
            hits == ["notes/beta.md"],
            f"hits={hits}",
        )

    # search_notes — type filter (combined with a non-empty query, since
    # the server requires `query` to be non-empty even with filters set).
    type_only = await h.call(
        "search_notes", query="needle", type_filter="organisation"
    )
    ok, why = expect_ok(type_only, where="search_notes type_filter")
    rep.add("search by type ok", ok, why)
    if ok:
        hits = _extract_paths(type_only)
        rep.add(
            "type=organisation, query=needle returns only org/acme.md",
            hits == ["org/acme.md"],
            f"hits={hits}",
        )

    # resolve_wikilink — alpha from index.md
    rw = await h.call("resolve_wikilink", target="alpha", from_path="index.md")
    ok, why = expect_ok(rw, where="resolve_wikilink alpha")
    rep.add("resolve_wikilink ok", ok, why)
    if ok:
        # `data.resolved` is the vault-relative posix path of the resolved
        # target (or None on miss / ambiguous).
        resolved = field_value(rw, "resolved")
        rep.add(
            "resolve [[alpha]] -> notes/alpha.md",
            resolved == "notes/alpha.md",
            f"got resolved={resolved!r} ambiguous={field_value(rw, 'ambiguous')!r}",
        )

    return rep


def _extract_paths(result) -> list[str]:
    """Pull a sorted list of vault-relative paths out of a search result."""
    matches = (result.data or {}).get("matches") or []
    return sorted({m.get("path") for m in matches if m.get("path")})
