"""End-to-end runner for `obsidian-full-mcp` v0.1.0.

Orchestrates scenarios S0-S9 against a fresh seeded test vault, talking
to the server through a real stdio MCP subprocess. Prints a final
table summarising pass/fail per scenario.

Usage:
    uv run python tests/e2e/run_e2e.py

Optional env vars:
    OBSIDIAN_E2E_REST_TOKEN  — enables the "with token" branch of S9
                                (needs Obsidian + Local REST API plugin)

Exit code: 0 if every scenario fully passes (SKIPPED rows count as
passing), 1 otherwise.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure scenarios/ and helpers can import each other directly.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import audit_inspector  # noqa: E402
from mcp_harness import E2EHarness  # noqa: E402
from scenarios import (  # noqa: E402
    s0_smoke,
    s1_read,
    s2_write,
    s3_frontmatter,
    s4_destructive,
    s5_path_sandbox,
    s6_yaml_safety,
    s7_validation_hooks,
    s8_audit,
    s9_rest,
)
from scenarios._assert import ScenarioReport  # noqa: E402
from seed_vault import seed  # noqa: E402


async def main() -> int:
    vault = HERE / ".test-vault"
    seed(vault)
    print(f"\nseeded vault: {vault}\n")

    audit_baseline = audit_inspector.line_count(
        audit_inspector.today_log_path()
    )

    reports: list[ScenarioReport] = []

    # Phase 1: scenarios that share a single long-lived harness (no
    # restart needed).
    async with E2EHarness(vault) as h:
        for fn in (
            s0_smoke.run,
            s1_read.run,
            s2_write.run,
            s3_frontmatter.run,
            s4_destructive.run,
            s5_path_sandbox.run,
            s6_yaml_safety.run,
        ):
            print(f"--- {fn.__module__} ---")
            rep = await fn(h)
            reports.append(rep)
            _print_scenario(rep)

        # S9 — REST branch (no-token uses current harness; with-token
        # opens a second one internally).
        print("--- s9_rest ---")
        rep = await s9_rest.run(h)
        reports.append(rep)
        _print_scenario(rep)

    # Phase 2: S7 spawns a fresh harness internally (restart required to
    # auto-load the dropped `.obsidian-full-mcp.yaml`).
    print("--- s7_validation_hooks ---")
    rep = await s7_validation_hooks.run(vault)
    reports.append(rep)
    _print_scenario(rep)

    # S8 — audit post-condition.
    print("--- s8_audit ---")
    rep = await s8_audit.run(audit_baseline)
    reports.append(rep)
    _print_scenario(rep)

    return _print_summary(reports)


def _print_scenario(rep: ScenarioReport) -> None:
    for step in rep.steps:
        mark = "✓" if step.ok else "✗"
        line = f"  {mark} {step.name}"
        if not step.ok and step.detail:
            line += f"  — {step.detail}"
        print(line)
    print(f"  → {rep.passed}/{rep.total}")
    print()


def _print_summary(reports: list[ScenarioReport]) -> int:
    print("=" * 72)
    print(f"{'Scenario':<28} {'Status':<10} {'Steps':>10}")
    print("-" * 72)
    total_ok = 0
    total_steps = 0
    failed = 0
    for r in reports:
        status = "PASS" if r.all_ok else "FAIL"
        if not r.all_ok:
            failed += 1
        total_ok += r.passed
        total_steps += r.total
        title = f"{r.code} — {r.title}"
        print(f"{title:<28} {status:<10} {r.passed}/{r.total:>4}")
    print("-" * 72)
    print(f"{'TOTAL':<28} {'PASS' if failed == 0 else 'FAIL':<10} {total_ok}/{total_steps}")
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
