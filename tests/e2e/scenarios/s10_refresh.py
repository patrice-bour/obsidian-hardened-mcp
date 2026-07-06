# SPDX-License-Identifier: Apache-2.0
"""S10 — vault-refresh: `list_stale_notes` over the real MCP wire.

The seeded vault carries one contracted note that is always overdue
(`refresh/stale-contract.md`, see `tests/e2e/seed_vault.py`). This
scenario exercises the full round trip: a read-only scan finds it,
`mark=true` stamps `refresh_due`/`refresh_stale` on disk (a real write
through the atomic writer), and a second `mark=true` run is a no-op
(idempotence)."""

from __future__ import annotations

from mcp_harness import E2EHarness

from ._assert import ScenarioReport, expect_ok, field_value

_STALE_PATH = "refresh/stale-contract.md"


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S10", "vault-refresh")

    # 1 — read-only scan finds the seeded stale contract, no write yet.
    scan = await h.call("list_stale_notes")
    ok, why = expect_ok(scan, where="list_stale_notes scan")
    rep.add("scan ok", ok, why)
    if ok:
        stale_paths = {e.get("path") for e in field_value(scan, "stale") or []}
        rep.add(
            "scan finds the seeded stale contract",
            _STALE_PATH in stale_paths,
            f"stale paths={stale_paths}",
        )
        rep.add(
            "scan performs no write (marked=0)",
            field_value(scan, "marked") == 0,
            f"got marked={field_value(scan, 'marked')!r}",
        )

    # 2 — mark=true stamps refresh_due/refresh_stale for at least our note.
    mark1 = await h.call("list_stale_notes", mark=True)
    ok, why = expect_ok(mark1, where="list_stale_notes mark=true (1st)")
    rep.add("first mark ok", ok, why)
    if ok:
        marked1 = field_value(mark1, "marked") or 0
        rep.add(
            "first mark stamps at least one note",
            marked1 >= 1,
            f"got marked={marked1!r}",
        )

    fm = await h.call("get_frontmatter", path=_STALE_PATH)
    ok, why = expect_ok(fm, where="get_frontmatter after mark")
    rep.add("frontmatter readable after mark", ok, why)
    if ok:
        front = field_value(fm, "frontmatter") or {}
        rep.add(
            "refresh_stale stamped True",
            front.get("refresh_stale") is True,
            f"got refresh_stale={front.get('refresh_stale')!r}",
        )
        rep.add(
            "refresh_due stamped",
            bool(front.get("refresh_due")),
            f"got refresh_due={front.get('refresh_due')!r}",
        )

    # 3 — second mark=true run is idempotent: nothing left to (re)write.
    mark2 = await h.call("list_stale_notes", mark=True)
    ok, why = expect_ok(mark2, where="list_stale_notes mark=true (2nd)")
    rep.add("second mark ok", ok, why)
    if ok:
        rep.add(
            "second mark is idempotent (marked=0)",
            field_value(mark2, "marked") == 0,
            f"got marked={field_value(mark2, 'marked')!r}",
        )

    return rep
