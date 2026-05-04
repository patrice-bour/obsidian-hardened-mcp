"""S3 — frontmatter atomic ops: set_frontmatter_field,
delete_frontmatter_field, merge_frontmatter (shallow / deep).

We target the seeded `frontmatter-rich.md` and verify that round-trip
preservation is honoured (comments, key order, quote styles of fields
NOT touched by the operation).
"""

from __future__ import annotations

import re

from mcp_harness import E2EHarness

from ._assert import ScenarioReport, expect_ok

TARGET = "frontmatter-rich.md"


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S3", "frontmatter")
    abs_path = h.vault / TARGET
    initial_text = abs_path.read_text(encoding="utf-8")

    # --- set_frontmatter_field --------------------------------------------
    s = await h.call(
        "set_frontmatter_field", path=TARGET, key="priority", value=2
    )
    ok, why = expect_ok(s, where="set_frontmatter_field")
    rep.add("set_frontmatter_field ok", ok, why)

    after_set = abs_path.read_text(encoding="utf-8")
    rep.add(
        "priority set to 2",
        re.search(r"priority:\s*2\b", after_set) is not None,
        f"section[:200]={_fm_section(after_set)[:200]!r}",
    )
    rep.add(
        "round-trip preserves top-level comment",
        "# top-level comment kept on round-trip" in after_set,
        "comment lost",
    )
    rep.add(
        "round-trip preserves status quote style",
        "status: 'draft'" in after_set,
        f"status line missing/changed: {_fm_section(after_set)[:200]!r}",
    )

    # --- delete_frontmatter_field -----------------------------------------
    d = await h.call(
        "delete_frontmatter_field", path=TARGET, key="priority"
    )
    ok, why = expect_ok(d, where="delete_frontmatter_field")
    rep.add("delete_frontmatter_field ok", ok, why)
    rep.add(
        "priority key gone",
        not re.search(r"^priority:", abs_path.read_text(), flags=re.MULTILINE),
        "priority still present",
    )

    # --- merge_frontmatter (shallow) --------------------------------------
    m = await h.call(
        "merge_frontmatter",
        path=TARGET,
        patch={"status": "review", "extra": "added"},
        mode="shallow",
    )
    ok, why = expect_ok(m, where="merge_frontmatter shallow")
    rep.add("merge_frontmatter shallow ok", ok, why)
    after_merge = abs_path.read_text(encoding="utf-8")
    # Accept review with or without surrounding quotes (the parser may keep
    # the original quote style on the value being replaced).
    rep.add(
        "shallow merge replaced status",
        re.search(r"^status:\s*['\"]?review['\"]?\s*", after_merge, flags=re.MULTILINE)
        is not None,
        f"status line: {_status_line(after_merge)!r}",
    )
    rep.add(
        "shallow merge added extra",
        re.search(r"^extra:\s*added", after_merge, flags=re.MULTILINE) is not None,
        "extra key not added",
    )

    # --- merge_frontmatter (deep) -----------------------------------------
    md = await h.call(
        "merge_frontmatter",
        path=TARGET,
        patch={"nested": {"gamma": 3}},
        mode="deep",
    )
    ok, why = expect_ok(md, where="merge_frontmatter deep")
    rep.add("merge_frontmatter deep ok", ok, why)
    final = abs_path.read_text(encoding="utf-8")
    nested_block = _nested_block(final)
    rep.add(
        "deep merge added nested.gamma",
        re.search(r"gamma:\s*3", nested_block) is not None,
        f"nested block: {nested_block!r}",
    )
    rep.add(
        "deep merge preserved nested.alpha",
        re.search(r"alpha:\s*1", nested_block) is not None,
        f"nested.alpha lost: {nested_block!r}",
    )
    rep.add(
        "deep merge preserved nested.beta",
        re.search(r"beta:\s*2", nested_block) is not None,
        f"nested.beta lost: {nested_block!r}",
    )

    # Restore the original file so other scenarios see a clean state.
    abs_path.write_text(initial_text, encoding="utf-8")
    return rep


def _fm_section(text: str) -> str:
    """Slice between the first two `---` markers; falls back to head."""
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[1]
    return text[:300]


def _status_line(text: str) -> str:
    """Pull the `status: ...` line out of the frontmatter for diagnostics."""
    section = _fm_section(text)
    match = re.search(r"^status:.*$", section, flags=re.MULTILINE)
    return match.group(0) if match else section[:120]


def _nested_block(text: str) -> str:
    """Return the YAML block under the `nested:` key (rough)."""
    section = _fm_section(text)
    match = re.search(r"nested:\n((?:[ \t]+.*\n)+)", section)
    return match.group(1) if match else section
