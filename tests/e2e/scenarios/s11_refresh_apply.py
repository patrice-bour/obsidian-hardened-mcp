# SPDX-License-Identifier: Apache-2.0
"""S11 — vault-refresh v2: `refresh_apply` over the real MCP wire.

The seeded vault carries a whitelist-pinned auto contract
(`refresh/auto-pinned.md`, task `e2e-auto-refresh` in
`.obsidian-hardened-mcp.yaml`, see `tests/e2e/seed_vault.py`) and a
flag-policy contract (`refresh/stale-contract.md`, reused from S10).

This scenario exercises the full round trip: `refresh_apply` succeeds on
the pinned auto note (body replaced, `refresh_last` advanced, snapshot
taken — verified via `get_frontmatter` and `read_note` after the call),
and is refused (`VALIDATION_FAILED`, zero side effects) on the flag note,
which has no `auto` contract to pin against."""

from __future__ import annotations

import datetime as dt

from mcp_harness import E2EHarness

from ._assert import ScenarioReport, expect_error, expect_ok, field_value

_AUTO_PATH = "refresh/auto-pinned.md"
_FLAG_PATH = "refresh/stale-contract.md"
_NEW_BODY = "# Fresh\n\nRe-checked and refreshed via refresh_apply.\n"


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S11", "refresh_apply")

    # 1 — apply refused on a flag-policy note: no auto contract to pin
    # against, so refresh_apply must refuse before any write.
    before = await h.call("read_note", path=_FLAG_PATH)
    ok, why = expect_ok(before, where="read_note flag note (pre)")
    rep.add("flag note readable before refusal", ok, why)
    original_flag_body = field_value(before, "content") or ""

    refused = await h.call("refresh_apply", path=_FLAG_PATH, body=_NEW_BODY)
    ok, why = expect_error(refused, "validation_failed", where="refresh_apply on flag note")
    rep.add("apply refused on flag-policy note", ok, why)

    after = await h.call("read_note", path=_FLAG_PATH)
    ok, why = expect_ok(after, where="read_note flag note (post)")
    rep.add("flag note still readable after refusal", ok, why)
    if ok:
        rep.add(
            "flag note body untouched by the refused apply",
            field_value(after, "content") == original_flag_body,
            "body changed despite refused apply",
        )

    # 2 — apply OK on the seeded pinned auto contract.
    applied = await h.call("refresh_apply", path=_AUTO_PATH, body=_NEW_BODY)
    ok, why = expect_ok(applied, where="refresh_apply on pinned auto note")
    rep.add("apply OK on pinned auto note", ok, why)
    if ok:
        rep.add(
            "response carries a non-null snapshot_id",
            bool(field_value(applied, "snapshot_id")),
            f"got snapshot_id={field_value(applied, 'snapshot_id')!r}",
        )
        today_iso = dt.date.today().isoformat()
        rep.add(
            "response reports refresh_last advanced to today",
            field_value(applied, "refresh_last") == today_iso,
            f"got refresh_last={field_value(applied, 'refresh_last')!r}",
        )

    # 3 — re-read via get_frontmatter: refresh_last/refresh_due/refresh_stale
    # are server-managed and must reflect the applied write on disk.
    fm = await h.call("get_frontmatter", path=_AUTO_PATH)
    ok, why = expect_ok(fm, where="get_frontmatter after apply")
    rep.add("frontmatter readable after apply", ok, why)
    if ok:
        front = field_value(fm, "frontmatter") or {}
        today_iso = dt.date.today().isoformat()
        rep.add(
            "refresh_last advanced on disk",
            str(front.get("refresh_last")) == today_iso,
            f"got refresh_last={front.get('refresh_last')!r}",
        )
        rep.add(
            "refresh_stale cleared on disk",
            front.get("refresh_stale") is False,
            f"got refresh_stale={front.get('refresh_stale')!r}",
        )
        rep.add(
            "refresh_task preserved",
            front.get("refresh_task") == "e2e-auto-refresh",
            f"got refresh_task={front.get('refresh_task')!r}",
        )

    # 4 — body actually replaced on disk.
    note = await h.call("read_note", path=_AUTO_PATH)
    ok, why = expect_ok(note, where="read_note after apply")
    rep.add("note readable after apply", ok, why)
    if ok:
        body = field_value(note, "content") or ""
        rep.add(
            "body replaced with the new content",
            "Re-checked and refreshed" in body and "Old body content" not in body,
            f"body[:120]={body[:120]!r}",
        )

    return rep
