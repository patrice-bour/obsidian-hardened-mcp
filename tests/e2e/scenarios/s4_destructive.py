"""S4 — destructive ops with 2-phase HMAC confirm.

Covers:
- delete_note: phase 1 -> token -> phase 2 via stdio succeeds (file removed)
  because require_elicitation defaults to false since v0.3.1. The stdio
  harness cannot render the elicit dialog, but with the default opt-out
  setting the server falls through to HMAC-only.
- rename_note + update_backlinks: rewrite of [[old]] in linked notes
- move_note + update_backlinks
- token tampering -> invalid_confirmation_token (HMAC check, since elicit
  gate is bypassed with require_elicitation=false default)
- token tampering: file preserved (disk untouched on any Phase 2 failure)

Note: Phase 2 delete coverage for strict mode (elicit accept/reject) is in
`tests/integration/test_server_elicit.py` with a mocked elicit-capable
client using require_elicitation=True.

Note: token EXPIRY (90s TTL) is covered in unit tests (`tests/unit/
test_confirm.py`); we skip it here to keep the E2E run fast.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from mcp_harness import E2EHarness

from ._assert import (
    ScenarioReport,
    expect_error,
    expect_ok,
    field_value,
)


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S4", "destructive")
    vault = h.vault

    # ---------- delete_note ----------
    # Use a throwaway file we can recreate freely.
    target = "scratch/to-delete.md"
    body = "---\ntype: note\n---\n\n# To delete\nfilling.\n"
    await h.call("create_note", path=target, content=body)

    # Phase 1
    p1 = await h.call("delete_note", path=target)
    ok, why = expect_ok(p1, where="delete phase 1")
    rep.add("delete phase 1 ok", ok, why)
    rep.add(
        "phase 1 returned confirm_token",
        bool(field_value(p1, "confirm_token")),
        "no token issued",
    )
    rep.add(
        "phase 1 disk untouched",
        (vault / target).exists(),
        "file removed before phase 2",
    )

    token = field_value(p1, "confirm_token")

    # Phase 2 via stdio harness: with require_elicitation=false (default since
    # v0.3.1), the elicit exception is swallowed and the op proceeds via HMAC
    # only. The file should be deleted and the result ok.
    p2_attempt = await h.call("delete_note", path=target, confirm_token=token)
    ok, why = expect_ok(p2_attempt, where="phase 2 via stdio (elicit opt-out default)")
    rep.add(
        "phase 2 succeeds via stdio with require_elicitation=false (v0.3.1)",
        ok,
        why,
    )
    rep.add(
        "phase 2 file removed",
        not (vault / target).exists(),
        "file still present after phase 2",
    )

    # ---------- rename_note + update_backlinks ----------
    # to-rename.md ⇄ to-move.md (each links the other).
    # IMPORTANT: phase 1 and phase 2 must have IDENTICAL args (the HMAC
    # token is bound to the params hash). Pass `update_backlinks=True`
    # to BOTH calls.
    p1r = await h.call(
        "rename_note",
        path="to-rename.md",
        new_name="renamed.md",
        update_backlinks=True,
    )
    ok, why = expect_ok(p1r, where="rename phase 1")
    rep.add("rename phase 1 ok", ok, why)
    rtoken = field_value(p1r, "confirm_token")
    p2r = await h.call(
        "rename_note",
        path="to-rename.md",
        new_name="renamed.md",
        update_backlinks=True,
        confirm_token=rtoken,
    )
    ok, why = expect_ok(p2r, where="rename phase 2")
    rep.add("rename phase 2 ok", ok, why)
    rep.add(
        "to-rename.md gone",
        not (vault / "to-rename.md").exists(),
        "old file still present",
    )
    rep.add(
        "renamed.md present",
        (vault / "renamed.md").exists(),
        "new file missing",
    )
    # Backlink rewrite: to-move.md should now link [[renamed]]
    if (vault / "to-move.md").exists():
        body = (vault / "to-move.md").read_text(encoding="utf-8")
        rep.add(
            "[[to-rename]] in to-move.md rewritten to [[renamed]]",
            "[[renamed]]" in body and "[[to-rename]]" not in body,
            f"to-move body[:200]={body[:200]!r}",
        )

    # ---------- move_note + update_backlinks ----------
    # Same constraint as rename: pass identical args to both phases.
    p1m = await h.call(
        "move_note",
        path="renamed.md",
        new_folder="org",
        update_backlinks=True,
    )
    ok, why = expect_ok(p1m, where="move phase 1")
    rep.add("move phase 1 ok", ok, why)
    mtoken = field_value(p1m, "confirm_token")
    p2m = await h.call(
        "move_note",
        path="renamed.md",
        new_folder="org",
        update_backlinks=True,
        confirm_token=mtoken,
    )
    ok, why = expect_ok(p2m, where="move phase 2")
    rep.add("move phase 2 ok", ok, why)
    moved_exists = (vault / "org" / "renamed.md").exists()
    root_still = (vault / "renamed.md").exists()
    rep.add(
        "moved to org/renamed.md",
        moved_exists and not root_still,
        f"after move: org/renamed.md={moved_exists} root/renamed.md={root_still}",
    )

    # ---------- token tampering ----------
    # Phase 2 with a tampered token: with require_elicitation=false (default
    # since v0.3.1), the elicit gate is bypassed and the HMAC check runs,
    # returning invalid_confirmation_token.
    target_t = "scratch/tamper.md"
    await h.call("create_note", path=target_t, content=body)
    p1t = await h.call("delete_note", path=target_t)
    real_token = field_value(p1t, "confirm_token") or ""
    # Build a fully-random base64url token of similar length.
    bad_token = secrets.token_urlsafe(64)[: len(real_token)]
    p2t = await h.call(
        "delete_note", path=target_t, confirm_token=bad_token
    )
    ok, why = expect_error(
        p2t, "invalid_confirmation_token", where="tampered token"
    )
    rep.add("tampered token: invalid_confirmation_token (HMAC gate)", ok, why)
    rep.add(
        "tampered token: file preserved",
        (vault / target_t).exists(),
        "file removed despite invalid token",
    )

    return rep


def _find_snapshot(trash: Path, snap_id: str | None) -> Path | None:
    if snap_id is None or not trash.exists():
        return None
    for p in trash.rglob("*"):
        if p.is_file() and snap_id in p.as_posix():
            return p
    # snapshot_id might be a directory marker; check directories too.
    for p in trash.iterdir():
        if snap_id in p.name:
            return p
    return None
