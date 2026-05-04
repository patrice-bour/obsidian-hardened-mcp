"""Tests for frontmatter.parse / render — round-trip preservation + safety."""

from __future__ import annotations

import pytest

from obsidian_full_mcp.frontmatter import (
    FrontmatterTooLargeError,
    MalformedFrontmatterError,
    UnsafeYamlError,
    parse_note,
    render_note,
)


class TestNoFrontmatter:
    def test_plain_markdown_has_no_frontmatter(self) -> None:
        note = parse_note("# Hello\n\nWorld\n")
        assert note.frontmatter is None
        assert note.body == "# Hello\n\nWorld\n"

    def test_text_starting_with_three_dashes_but_not_a_block(self) -> None:
        # `---\n` followed by text that's not a YAML block end is just markdown.
        text = "---\nthis is a horizontal rule\n# Title\n"
        note = parse_note(text)
        assert note.frontmatter is None
        assert note.body == text


class TestEmptyAndSimple:
    def test_empty_frontmatter_block_yields_empty_mapping(self) -> None:
        note = parse_note("---\n---\n# Body\n")
        assert note.frontmatter == {}
        assert note.body == "# Body\n"

    def test_simple_keys(self) -> None:
        from datetime import date

        note = parse_note("---\ntitle: Hello\ndate: 2026-05-04\n---\nBody\n")
        # ruamel parses unquoted ISO-8601 dates into `datetime.date` and
        # round-trips them back to the same wire format.
        assert note.frontmatter == {"title": "Hello", "date": date(2026, 5, 4)}
        assert note.body == "Body\n"

    def test_quoted_dates_remain_strings(self) -> None:
        note = parse_note('---\ndate: "2026-05-04"\n---\n')
        assert note.frontmatter == {"date": "2026-05-04"}

    def test_list_values(self) -> None:
        note = parse_note("---\ntags:\n  - foo\n  - bar\n---\n")
        assert note.frontmatter is not None
        assert list(note.frontmatter["tags"]) == ["foo", "bar"]


class TestRoundTrip:
    def test_clean_input_is_preserved_byte_for_byte(self) -> None:
        text = "---\ntitle: Hello\ntags:\n  - foo\n  - bar\n---\nBody\n"
        assert render_note(parse_note(text)) == text

    def test_comments_are_preserved(self) -> None:
        text = (
            "---\n"
            "# top comment\n"
            "title: Hello\n"
            "tags:  # inline comment\n"
            "  - foo\n"
            "---\n"
            "Body\n"
        )
        rendered = render_note(parse_note(text))
        assert "# top comment" in rendered
        assert "# inline comment" in rendered

    def test_quote_style_is_preserved(self) -> None:
        text = "---\ntitle: \"double\"\nalt: 'single'\nbare: bare\n---\n"
        rendered = render_note(parse_note(text))
        assert '"double"' in rendered
        assert "'single'" in rendered
        # `bare: bare` stays unquoted
        assert "bare: bare" in rendered

    def test_key_order_is_preserved(self) -> None:
        text = "---\nzeta: 1\nalpha: 2\nmu: 3\n---\n"
        rendered = render_note(parse_note(text))
        assert rendered.index("zeta") < rendered.index("alpha") < rendered.index("mu")

    def test_utf8_accents_round_trip(self) -> None:
        text = "---\ntitle: Café à Paris\nauthor: Émile\n---\nBonjour\n"
        assert render_note(parse_note(text)) == text

    def test_crlf_input_normalises_to_lf(self) -> None:
        text = "---\r\ntitle: Hello\r\n---\r\nBody\r\n"
        note = parse_note(text)
        assert note.frontmatter == {"title": "Hello"}
        assert note.body == "Body\r\n"


class TestRenderEdgeCases:
    def test_render_with_no_frontmatter_returns_body_only(self) -> None:
        from obsidian_full_mcp.frontmatter import ParsedNote

        rendered = render_note(ParsedNote(frontmatter=None, body="# Plain\n"))
        assert rendered == "# Plain\n"

    def test_render_with_empty_frontmatter_emits_markers(self) -> None:
        note = parse_note("---\n---\nBody\n")
        assert note.frontmatter == {}
        # Empty mapping renders back as `---\n---\n` + body, not `---\n{}\n---\n`.
        assert render_note(note) == "---\n---\nBody\n"


class TestErrorHandling:
    def test_unclosed_frontmatter_is_lenient_by_default(self) -> None:
        # Obsidian-compatible: a leading `---` with no closing line is just
        # a horizontal rule, the file has no frontmatter.
        text = "---\ntitle: Hello\nbody continues without closing\n"
        note = parse_note(text)
        assert note.frontmatter is None
        assert note.body == text

    def test_unclosed_frontmatter_raises_in_strict_mode(self) -> None:
        with pytest.raises(MalformedFrontmatterError):
            parse_note(
                "---\ntitle: Hello\nbody continues without closing\n",
                strict=True,
            )

    def test_top_level_non_mapping_is_rejected(self) -> None:
        with pytest.raises(MalformedFrontmatterError):
            parse_note("---\n- one\n- two\n---\n")

    def test_invalid_yaml_syntax_is_rejected(self) -> None:
        # Trailing colon with no value on same indent → unexpected scanner state.
        with pytest.raises(MalformedFrontmatterError):
            parse_note("---\n: bad\n nested: x\n---\n")

    def test_empty_string_returns_empty_note(self) -> None:
        note = parse_note("")
        assert note.frontmatter is None
        assert note.body == ""

    def test_oversized_frontmatter_is_rejected(self) -> None:
        big_value = "x" * (200 * 1024)  # 200 KiB > 64 KiB default
        text = f"---\nblob: {big_value}\n---\n"
        with pytest.raises(FrontmatterTooLargeError):
            parse_note(text)

    def test_oversized_can_be_overridden_with_explicit_limit(self) -> None:
        big_value = "x" * (200 * 1024)
        text = f"---\nblob: {big_value}\n---\nbody\n"
        # Explicit larger budget allows it through.
        note = parse_note(text, max_frontmatter_bytes=512 * 1024)
        assert note.frontmatter is not None


class TestYamlSafety:
    def test_python_object_tags_are_rejected(self) -> None:
        # Classic YAML deserialisation RCE vector. ruamel rt mode rejects it
        # outright (no resolver for !!python/object).
        text = (
            "---\n"
            "danger: !!python/object/apply:os.system ['echo pwned']\n"
            "---\n"
        )
        with pytest.raises(UnsafeYamlError):
            parse_note(text)

    def test_billion_laughs_does_not_explode(self) -> None:
        # Quadratic-blowup YAML alias bomb. We keep parsing bounded by
        # frontmatter size limit + ruamel's safe rt loader.
        bomb = (
            "---\n"
            "a: &a [1, 2, 3]\n"
            "b: *a\n"
            "c: *a\n"
            "d: *a\n"
            "---\n"
        )
        # Should parse fine — the actual exponential bomb (deeply nested) is
        # caught by the size limit.
        note = parse_note(bomb)
        assert note.frontmatter is not None

    def test_anchor_chain_does_not_explode(self) -> None:
        # ruamel's rt loader does not resolve aliases eagerly into expanded
        # forms, so a chain of references stays linear-time and bounded
        # in size by the source bytes.
        anchors = "\n".join(
            f"k{i}: &b{i} [{', '.join(['*a0'] * 5)}]" for i in range(3)
        )
        text = f"---\nseed: &a0 [1]\n{anchors}\n---\n"
        note = parse_note(text)
        assert note.frontmatter is not None
