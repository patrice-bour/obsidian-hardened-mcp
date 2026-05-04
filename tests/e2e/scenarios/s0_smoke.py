"""S0 — smoke: server boots, lists 18 tools, get_vault_info matches."""

from __future__ import annotations

from mcp_harness import E2EHarness

from ._assert import (
    ScenarioReport,
    expect_data_contains,
    expect_ok,
    field_value,
)

EXPECTED_TOOLS = {
    "read_note",
    "list_notes",
    "get_frontmatter",
    "search_notes",
    "resolve_wikilink",
    "create_note",
    "update_note",
    "append_to_note",
    "patch_note",
    "set_frontmatter_field",
    "delete_frontmatter_field",
    "merge_frontmatter",
    "delete_note",
    "rename_note",
    "move_note",
    "execute_command",
    "get_vault_info",
    "list_tools_capabilities",
}


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S0", "smoke")

    # 1 — 18 tools registered
    names = {t["name"] for t in h.tools}
    missing = sorted(EXPECTED_TOOLS - names)
    extra = sorted(names - EXPECTED_TOOLS)
    rep.add(
        "18 tools registered",
        names == EXPECTED_TOOLS,
        f"got {len(names)} tools; missing={missing} extra={extra}",
    )

    # 2 — get_vault_info ok and points to our vault
    info = await h.call("get_vault_info")
    ok, why = expect_ok(info, where="get_vault_info")
    rep.add("get_vault_info ok", ok, why)
    if ok:
        rep.add(
            "vault_root matches",
            str(h.vault) == field_value(info, "vault_root"),
            f"want {h.vault}, got {field_value(info, 'vault_root')}",
        )
        rep.add(
            "rest_available=False (no token)",
            field_value(info, "rest_available") is False,
            f"got {field_value(info, 'rest_available')!r}",
        )

    # 3 — list_tools_capabilities surfaces all kinds
    caps = await h.call("list_tools_capabilities")
    ok, why = expect_ok(caps, where="list_tools_capabilities")
    rep.add("list_tools_capabilities ok", ok, why)
    if ok:
        ok, why = expect_data_contains(caps, "tools", where="caps")
        rep.add("capabilities has 'tools' field", ok, why)

    return rep
