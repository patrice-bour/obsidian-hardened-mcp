"""S9 — REST API branch: with no `OBSIDIAN_REST_TOKEN` set,
`execute_command` must short-circuit with `rest_unavailable`.

The "with token" sub-scenario is opt-in via the `OBSIDIAN_E2E_REST_TOKEN`
env var. It requires a running Obsidian instance with the Local REST
API plugin enabled. When the env var is absent we mark that step as
SKIPPED rather than failing.
"""

from __future__ import annotations

import os

from mcp_harness import E2EHarness

from ._assert import ScenarioReport, expect_error


async def run(h: E2EHarness) -> ScenarioReport:
    rep = ScenarioReport("S9", "rest api")

    # No-token branch
    r = await h.call("execute_command", command_id="editor:focus")
    ok, why = expect_error(
        r, "rest_unavailable", where="execute_command without token"
    )
    rep.add("execute_command rejects when REST disabled", ok, why)

    # With-token branch (opt-in)
    if os.getenv("OBSIDIAN_E2E_REST_TOKEN") is None:
        rep.add(
            "execute_command (with token) — SKIPPED",
            True,
            "set OBSIDIAN_E2E_REST_TOKEN to enable; needs Obsidian + Local REST API plugin",
        )
    else:
        # We need to spawn a SECOND harness that propagates the token.
        token = os.environ["OBSIDIAN_E2E_REST_TOKEN"]
        try:
            async with E2EHarness(
                h.vault, env_overrides={"OBSIDIAN_REST_TOKEN": token}
            ) as h2:
                p1 = await h2.call(
                    "execute_command", command_id="app:show-release-notes"
                )
                rep.add(
                    "execute_command phase 1 with token ok",
                    p1.ok,
                    f"got ok={p1.ok} code={p1.error_code} msg={p1.error_message!r}",
                )
                if p1.ok:
                    tok = (p1.data or {}).get("confirm_token")
                    p2 = await h2.call(
                        "execute_command",
                        command_id="app:show-release-notes",
                        confirm_token=tok,
                    )
                    rep.add(
                        "execute_command phase 2 with token ok",
                        p2.ok,
                        f"got ok={p2.ok} code={p2.error_code} msg={p2.error_message!r}",
                    )
        except Exception as exc:  # pragma: no cover
            rep.add(
                "execute_command with token (Obsidian probably not running)",
                False,
                f"exception: {type(exc).__name__}: {exc}",
            )

    return rep
