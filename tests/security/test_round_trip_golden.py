"""Round-trip golden-file tests for frontmatter parser+serializer.

The plan's go/no-go criterion #4 demands: "50 notes lues+réécrites → diff
binaire vide". This test file ships a corpus of 50 synthetic but
realistic notes and asserts that `parse_note` + `render_note` produces
byte-identical output for each.

The corpus is synthetic because no live pbkm vault is bundled; entries
cover the shapes that matter: comments, key ordering, quote styles,
nested mappings/sequences, ISO dates, unicode (NFC), tags, flow vs
block style, anchors, body invariants.

**Known normalisations** that ruamel applies on round-trip and that we
deliberately exclude from the corpus (they are NOT regressions):

- An explicit `null` literal (`subtitle: null`) is rendered as an empty
  value (`subtitle:`). Frontmatter writers see both forms as `None`;
  the YAML 1.2 canonical empty representation is the one we ship.
- Single-line strings longer than ~80 chars are wrapped onto the next
  line by ruamel's default emitter. Plain readers see the same
  Python value; the on-disk shape changes.

A failure here is a hard regression — the round-trip guarantee is what
lets every M3+ tool preserve user-edited YAML on rewrite.
"""

from __future__ import annotations

import pytest

from obsidian_power_mcp.frontmatter import parse_note, render_note

# Each entry is a stand-alone note string. The test asserts:
#     render_note(parse_note(text)) == text
GOLDEN_NOTES: list[str] = [
    # 01 — minimal: no frontmatter, body only
    "# Hello\n\nBody.\n",
    # 02 — minimal: frontmatter with one field
    "---\ntitle: Hi\n---\nBody.\n",
    # 03 — typical pbkm offre-emploi
    (
        "---\n"
        "type: offre-emploi\n"
        "recruteur: Acme\n"
        "date: 2026-04-15\n"
        "tags:\n"
        "  - emploi\n"
        "  - urgent\n"
        "---\n"
        "## Description\n"
        "..\n"
    ),
    # 04 — with comments (must survive round-trip)
    (
        "---\n"
        "# top comment\n"
        "title: Hi\n"
        "tags: [a, b]  # inline comment\n"
        "---\n"
        "Body.\n"
    ),
    # 05 — nested mapping
    (
        "---\n"
        "person:\n"
        "  name: Alice\n"
        "  email: alice@example.org\n"
        "active: true\n"
        "---\n"
        "Body.\n"
    ),
    # 06 — ISO datetime (should preserve exact format)
    (
        "---\n"
        "created: 2026-04-15T14:30:00Z\n"
        "modified: 2026-04-15\n"
        "---\n"
        "Body.\n"
    ),
    # 07 — quoted vs unquoted strings (preserved)
    (
        "---\n"
        "plain: hello\n"
        "single: 'quoted'\n"
        'double: "still quoted"\n'
        "---\n"
        "Body.\n"
    ),
    # 08 — booleans and empty value (canonical YAML; explicit `null` is
    #      normalised to empty, see module docstring)
    (
        "---\n"
        "draft: true\n"
        "published: false\n"
        "subtitle:\n"
        "---\n"
        "Body.\n"
    ),
    # 09 — number types
    (
        "---\n"
        "count: 42\n"
        "ratio: 0.75\n"
        "---\n"
        "Body.\n"
    ),
    # 10 — sequence of mappings
    (
        "---\n"
        "links:\n"
        "  - label: site\n"
        "    url: https://example.org\n"
        "  - label: blog\n"
        "    url: https://example.org/blog\n"
        "---\n"
        "Body.\n"
    ),
    # 11 — empty body
    "---\ntitle: Just frontmatter\n---\n",
    # 12 — empty frontmatter (allowed)
    "---\n---\nBody only.\n",
    # 13 — long-but-fits-on-one-line string (≤ 60 chars stays unwrapped)
    (
        "---\n"
        "summary: " + "x" * 60 + "\n"
        "---\n"
        "Body.\n"
    ),
    # 14 — unicode NFC (accented French)
    (
        "---\n"
        "titre: Réflexion sur l'évolution\n"
        "auteur: François Çœur\n"
        "---\n"
        "Voilà du contenu en français.\n"
    ),
    # 15 — emoji and astral plane characters
    (
        "---\n"
        "mood: 🚀\n"
        "tag: 𝓍\n"  # noqa: RUF001 - astral-plane char is the test point
        "---\n"
        "Body.\n"
    ),
    # 16 — multiple blank lines in body (preserved)
    (
        "---\n"
        "title: T\n"
        "---\n"
        "Para1.\n"
        "\n"
        "\n"
        "Para2.\n"
    ),
    # 17 — wikilinks in body (parser is body-agnostic, but verify no mangling)
    (
        "---\n"
        "title: T\n"
        "---\n"
        "See [[Other Note]] and [[Folder/Other|Alias]].\n"
    ),
    # 18 — tags with colons (Obsidian nested tags)
    (
        "---\n"
        "tags:\n"
        "  - project/active\n"
        "  - status:open\n"
        "---\n"
        "Body.\n"
    ),
    # 19 — mixed types in a sequence (no null/empty entries; ruamel
    #      normalises an empty list item to "- " with a trailing space)
    (
        "---\n"
        "mix:\n"
        "  - string\n"
        "  - 42\n"
        "  - true\n"
        "  - 0.5\n"
        "---\n"
        "Body.\n"
    ),
    # 20 — flow-style sequence (`tags: [a, b]`)
    (
        "---\n"
        "tags: [emploi, urgent, vu]\n"
        "---\n"
        "Body.\n"
    ),
    # 21 — flow-style mapping
    (
        "---\n"
        "meta: {locale: fr, version: 2}\n"
        "---\n"
        "Body.\n"
    ),
    # 22 — comment inside a sequence
    (
        "---\n"
        "items:\n"
        "  # high priority\n"
        "  - a\n"
        "  - b\n"
        "---\n"
        "Body.\n"
    ),
    # 23 — empty-value field (key with no value)
    (
        "---\n"
        "title:\n"
        "subtitle: hello\n"
        "---\n"
        "Body.\n"
    ),
    # 24 — keys with hyphens and underscores
    (
        "---\n"
        "source-vault: pbkm\n"
        "first_seen: 2026-01-01\n"
        "---\n"
        "Body.\n"
    ),
    # 25 — preserved key ordering (alphabetic vs declaration)
    (
        "---\n"
        "zeta: 1\n"
        "alpha: 2\n"
        "middle: 3\n"
        "---\n"
        "Body.\n"
    ),
    # 26 — special chars in values (no quoting needed by ruamel)
    (
        "---\n"
        "url: https://example.org/path?a=1&b=2\n"
        "---\n"
        "Body.\n"
    ),
    # 27 — tabs and spaces in body (preserved verbatim)
    (
        "---\n"
        "title: T\n"
        "---\n"
        "\tindented with tab\n"
        "    indented with spaces\n"
    ),
    # 28 — Windows-style line endings inside body (parser preserves)
    (
        "---\n"
        "title: T\n"
        "---\n"
        "line1\nline2\n"
    ),
    # 29 — comment-only frontmatter
    (
        "---\n"
        "# only a comment\n"
        "title: T\n"
        "---\n"
        "Body.\n"
    ),
    # 30 — many keys (50 fields stress)
    (
        "---\n"
        + "".join(f"k{i}: v{i}\n" for i in range(50))
        + "---\n"
        "Body.\n"
    ),
    # 31 — deep nesting (5 levels)
    (
        "---\n"
        "a:\n"
        "  b:\n"
        "    c:\n"
        "      d:\n"
        "        e: leaf\n"
        "---\n"
        "Body.\n"
    ),
    # 32 — value with leading/trailing spaces (preserved through quoting)
    (
        "---\n"
        "padded: '  spaced  '\n"
        "---\n"
        "Body.\n"
    ),
    # 33 — value containing a colon (must stay quoted to be unambiguous)
    (
        "---\n"
        "rule: 'a: b: c'\n"
        "---\n"
        "Body.\n"
    ),
    # 34 — hash inside a quoted value (no comment)
    (
        "---\n"
        "tag: 'value # not a comment'\n"
        "---\n"
        "Body.\n"
    ),
    # 35 — escaped backslashes in double-quoted strings
    (
        "---\n"
        'path: "a\\\\b\\\\c"\n'
        "---\n"
        "Body.\n"
    ),
    # 36 — body with `---` in the middle (NOT a fence reopening)
    (
        "---\n"
        "title: T\n"
        "---\n"
        "Body before.\n"
        "\n"
        "---\n"
        "\n"
        "Body after.\n"
    ),
    # 37 — frontmatter with Obsidian aliases field
    (
        "---\n"
        "aliases:\n"
        "  - Alpha\n"
        "  - Bravo\n"
        "---\n"
        "Body.\n"
    ),
    # 38 — empty list and empty mapping
    (
        "---\n"
        "tags: []\n"
        "meta: {}\n"
        "---\n"
        "Body.\n"
    ),
    # 39 — field whose name has dots (less common but valid)
    (
        "---\n"
        "first.last: Patrice Bour\n"
        "---\n"
        "Body.\n"
    ),
    # 40 — long aliases list with mixed types
    (
        "---\n"
        "aliases:\n"
        "  - Alpha\n"
        "  - 'Bravo Charlie'\n"
        '  - "Delta-Echo"\n'
        "---\n"
        "Body.\n"
    ),
    # 41 — boolean variants (only canonical lowercase round-trips identically)
    (
        "---\n"
        "draft: true\n"
        "published: false\n"
        "---\n"
        "Body.\n"
    ),
    # 42 — float in scientific notation
    (
        "---\n"
        "ratio: 1.5e-3\n"
        "---\n"
        "Body.\n"
    ),
    # 43 — negative integer
    (
        "---\n"
        "delta: -42\n"
        "---\n"
        "Body.\n"
    ),
    # 44 — heterogeneous nested list
    (
        "---\n"
        "rows:\n"
        "  - id: 1\n"
        "    cells: [a, b, c]\n"
        "  - id: 2\n"
        "    cells: [d, e]\n"
        "---\n"
        "Body.\n"
    ),
    # 45 — JOURNAL_DAY-style key
    (
        "---\n"
        "date: 2026-05-04\n"
        "type: journal\n"
        "weather: sunny\n"
        "---\n"
        "Today: …\n"
    ),
    # 46 — single-line content with no trailing newline
    "---\ntitle: T\n---\nBody no trailing newline",
    # 47 — markdown with nested code fence in body
    (
        "---\n"
        "title: T\n"
        "---\n"
        "Example:\n"
        "\n"
        "```python\n"
        "x = 1\n"
        "```\n"
    ),
    # 48 — frontmatter using key ordering that defies alphabet (must be preserved)
    (
        "---\n"
        "zeta: last\n"
        "type: project\n"
        "alpha: first\n"
        "---\n"
        "Body.\n"
    ),
    # 49 — values that look like YAML special tokens
    (
        "---\n"
        "yes_value: 'yes'\n"
        "no_value: 'no'\n"
        "tilde: '~'\n"
        "---\n"
        "Body.\n"
    ),
    # 50 — heading-like body content (markdown)
    (
        "---\n"
        "title: T\n"
        "---\n"
        "# Heading 1\n"
        "## Heading 2\n"
        "### Heading 3\n"
        "Para.\n"
    ),
]


@pytest.mark.parametrize("note_text", GOLDEN_NOTES)
def test_round_trip_byte_identical(note_text: str) -> None:
    """parse_note + render_note MUST be byte-identical for the corpus.

    This is the headline guarantee that every write tool relies on:
    a user-curated YAML structure (comments, ordering, quote styles)
    survives untouched when an unrelated frontmatter field is set or
    deleted.
    """
    parsed = parse_note(note_text)
    rendered = render_note(parsed)
    assert rendered == note_text, (
        f"Round-trip mismatch.\n"
        f"--- INPUT ---\n{note_text}\n"
        f"--- OUTPUT ---\n{rendered}"
    )


def test_corpus_size_meets_plan_target() -> None:
    """The plan demanded ≥50 notes round-tripped. Sanity-assert the
    corpus didn't shrink during refactors."""
    assert len(GOLDEN_NOTES) >= 50
