"""S6 — YAML safety: a note with a non-default YAML tag in its
frontmatter must be rejected by the parser at read time.

The note is seeded directly to disk (the server itself refuses to
write such a payload).
"""

from __future__ import annotations

from mcp_harness import E2EHarness

from ._assert import ScenarioReport

_UNSAFE_NOTE = (
    "---\n"
    "title: Unsafe\n"
    "weapon: !!python/object/apply:os.system [echo pwned]\n"
    "---\n\n"
    "# Unsafe note\n"
)


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S6", "yaml safety")

    target = h.vault / "unsafe-yaml.md"
    target.write_text(_UNSAFE_NOTE, encoding="utf-8")

    # read_note returns the raw text; the parser doesn't run on read_note.
    # The actual safety net is on the *frontmatter* path, used by anything
    # that parses the YAML block.
    fm = await h.call("get_frontmatter", path="unsafe-yaml.md")
    rep.add(
        "get_frontmatter rejects unsafe YAML tag",
        (not fm.ok) and fm.error_code in ("unsafe_yaml", "malformed_frontmatter"),
        f"got ok={fm.ok} code={fm.error_code!r} msg={fm.error_message!r}",
    )

    sf = await h.call(
        "set_frontmatter_field",
        path="unsafe-yaml.md",
        key="title",
        value="Sanitized",
    )
    rep.add(
        "set_frontmatter_field on unsafe file is rejected",
        (not sf.ok) and sf.error_code in ("unsafe_yaml", "malformed_frontmatter"),
        f"got ok={sf.ok} code={sf.error_code!r}",
    )

    target.unlink()
    return rep
