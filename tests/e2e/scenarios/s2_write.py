"""S2 — write: create_note, update_note, append_to_note, patch_note.

Each tool is exercised twice:
- dry_run=True → server reports the intended write but disk stays untouched
- dry_run=False → relecture pour confirmer l'effet, puis cleanup
"""

from __future__ import annotations

from mcp_harness import E2EHarness

from ._assert import ScenarioReport, expect_ok


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S2", "write")
    vault = h.vault

    # --- create_note -------------------------------------------------------
    target = "scratch/created.md"
    target_abs = vault / target
    body_v1 = "---\ntype: note\n---\n\n# Created\nFirst line.\n"

    dry = await h.call("create_note", path=target, content=body_v1, dry_run=True)
    ok, why = expect_ok(dry, where="create_note dry_run")
    rep.add("create_note dry_run ok", ok, why)
    rep.add(
        "create_note dry_run leaves disk untouched",
        not target_abs.exists(),
        f"unexpectedly created {target_abs}",
    )

    # Snapshot the parent dir before the real write so we can assert no
    # extra files appeared (atomic-write tmp files should be gone, not
    # left behind). A glob with a guessed prefix would pass vacuously
    # if the project's tmp pattern ever changed.
    parent = target_abs.parent
    parent.mkdir(parents=True, exist_ok=True)
    before = set(parent.iterdir())

    real = await h.call("create_note", path=target, content=body_v1)
    ok, why = expect_ok(real, where="create_note real")
    rep.add("create_note real ok", ok, why)
    rep.add(
        "create_note wrote file",
        target_abs.exists() and target_abs.read_text() == body_v1,
        f"exists={target_abs.exists()}",
    )

    after = set(parent.iterdir())
    extras = after - before - {target_abs}
    rep.add(
        "no orphan tmp file after atomic write",
        not extras,
        f"unexpected files: {[p.name for p in extras]}",
    )

    # --- update_note -------------------------------------------------------
    body_v2 = "---\ntype: note\n---\n\n# Created\nReplaced content.\n"
    upd = await h.call("update_note", path=target, content=body_v2)
    ok, why = expect_ok(upd, where="update_note")
    rep.add("update_note ok", ok, why)
    rep.add(
        "update_note replaced content",
        target_abs.read_text() == body_v2,
        f"got {target_abs.read_text()!r}",
    )

    # --- append_to_note ----------------------------------------------------
    add = "Appended line.\n"
    app = await h.call("append_to_note", path=target, content=add)
    ok, why = expect_ok(app, where="append_to_note")
    rep.add("append_to_note ok", ok, why)
    rep.add(
        "append_to_note added content",
        target_abs.read_text().endswith(add),
        f"tail={target_abs.read_text()[-30:]!r}",
    )

    # --- patch_note --------------------------------------------------------
    p = await h.call(
        "patch_note", path=target, find="Replaced content", replace="Patched body"
    )
    ok, why = expect_ok(p, where="patch_note")
    rep.add("patch_note ok", ok, why)
    rep.add(
        "patch_note swapped substring",
        "Patched body" in target_abs.read_text()
        and "Replaced content" not in target_abs.read_text(),
        f"body[:80]={target_abs.read_text()[:80]!r}",
    )

    # patch with count=1 but two occurrences should fail with PATCH_COUNT_MISMATCH.
    # Set up a body with two matches, then probe.
    multi_body = (
        "---\ntype: note\n---\n\n# Multi\n"
        "alpha BANANA beta\n"
        "gamma BANANA delta\n"
    )
    multi_path = "scratch/multi.md"
    multi_abs = vault / multi_path
    await h.call("create_note", path=multi_path, content=multi_body)
    pmismatch = await h.call(
        "patch_note", path=multi_path, find="BANANA", replace="X", count=1
    )
    rep.add(
        "patch_note count=1 with 2 matches -> PATCH_COUNT_MISMATCH",
        not pmismatch.ok and pmismatch.error_code == "patch_count_mismatch",
        f"got ok={pmismatch.ok} code={pmismatch.error_code}",
    )
    # Multi-replace via count=0 (all)
    pall = await h.call(
        "patch_note", path=multi_path, find="BANANA", replace="X", count=0
    )
    rep.add(
        "patch_note count=0 replaces all",
        pall.ok and "BANANA" not in multi_abs.read_text(),
        f"ok={pall.ok} body={multi_abs.read_text()[:80]!r}",
    )

    return rep
