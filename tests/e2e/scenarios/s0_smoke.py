"""S0 — smoke: server boots, lists the expected toolset, get_vault_info matches."""

from __future__ import annotations

from mcp_harness import E2EHarness

from ._assert import (
    ScenarioReport,
    expect_data_contains,
    expect_ok,
    field_value,
)

# Baseline of tools that v0.1 must always expose. Subset check below —
# adding tools in v0.2+ must not break S0; new tools should be added
# here in the same commit that registers them server-side.
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

    # 1 — every baseline tool is registered (superset, not equality)
    names = {t["name"] for t in h.tools}
    missing = sorted(EXPECTED_TOOLS - names)
    rep.add(
        "baseline tools registered",
        names >= EXPECTED_TOOLS,
        f"got {len(names)} tools; missing={missing}",
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

    # 3 — list_tools_capabilities ok, has 'tools' field, and reports the
    # SAME set as the MCP initialise/list_tools handshake (no drift
    # between the two surfaces).
    caps = await h.call("list_tools_capabilities")
    ok, why = expect_ok(caps, where="list_tools_capabilities")
    rep.add("list_tools_capabilities ok", ok, why)
    if ok:
        ok, why = expect_data_contains(caps, "tools", where="caps")
        rep.add("capabilities has 'tools' field", ok, why)
        cap_names = {t["name"] for t in (caps.data or {}).get("tools", [])}
        rep.add(
            "list_tools_capabilities matches MCP list_tools",
            cap_names == names,
            f"caps={sorted(cap_names)} list_tools={sorted(names)}",
        )

    return rep
