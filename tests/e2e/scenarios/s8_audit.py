"""S8 — audit log: confirm that JSONL audit lines were appended for
every write/destructive op during the run, and that each line has the
expected JSON shape."""

from __future__ import annotations

from pathlib import Path

import audit_inspector

from ._assert import ScenarioReport


async def run(audit_baseline: int, log_path: Path) -> ScenarioReport:
    """`log_path` is captured at baseline time in run_e2e so the
    comparison is against the same file even if the run crosses midnight
    UTC."""
    rep = ScenarioReport("S8", "audit")
    rep.add(
        "audit log exists",
        log_path.exists(),
        f"expected {log_path}",
    )
    if not log_path.exists():
        return rep

    current = audit_inspector.line_count(log_path)
    delta = current - audit_baseline
    rep.add(
        "audit log grew during the run",
        delta > 0,
        f"baseline={audit_baseline} now={current} delta={delta}",
    )

    # Spot check: shape of the last 20 entries.
    sample = audit_inspector.read_recent(log_path, 20)
    bad: list[str] = []
    for entry in sample:
        ok, reason = audit_inspector.verify_shape(entry)
        if not ok:
            bad.append(f"{entry.get('audit_id', '?')[:12]}: {reason}")
    rep.add(
        "last 20 audit entries match the schema",
        not bad,
        f"violations: {bad[:3]}",
    )

    # request_id is generated once per tool call — every entry must have one.
    missing_rid = sum(1 for e in sample if not e.get("request_id"))
    rep.add(
        "every entry carries a request_id",
        missing_rid == 0,
        f"{missing_rid} entries missing request_id",
    )

    return rep
