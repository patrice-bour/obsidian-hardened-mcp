"""`run_cycle` — vault-only refresh execution core (vault-refresh v2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from refresh_executor.core import run_cycle

TODAY = date(2026, 7, 6)


def fake_llm(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
    return "# Refreshed\n\nNew generated body, long enough to pass ratio.\n", 0.0


class TestRunCycle:
    def test_applies_executable_task(self, exec_vault: Path) -> None:
        report = run_cycle(exec_vault, llm_complete=fake_llm, today=TODAY)
        [res] = [r for r in report.results if r.task_id == "t1"]
        assert res.status == "applied" and res.cost == 0.0
        text = (exec_vault / "01_Notes" / "auto.md").read_text()
        assert "New generated body" in text and "refresh_stale: false" in text.lower()

    def test_dry_run_writes_nothing(self, exec_vault: Path) -> None:
        before = (exec_vault / "01_Notes" / "auto.md").read_text()
        report = run_cycle(exec_vault, llm_complete=fake_llm, today=TODAY, dry_run=True)
        assert report.results and (exec_vault / "01_Notes" / "auto.md").read_text() == before

    def test_output_guards_reject_short_body(self, exec_vault: Path) -> None:
        def tiny(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
            return "x", 0.0
        report = run_cycle(exec_vault, llm_complete=tiny, today=TODAY)
        [res] = report.results
        assert res.status == "anomaly" and "body" in res.reason

    def test_llm_error_isolated_per_task(self, exec_vault_two_tasks: Path) -> None:
        def flaky(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
            if "boom" in messages[-1]["content"]:
                raise RuntimeError("llm down")
            return fake_llm(route, messages)
        report = run_cycle(exec_vault_two_tasks, llm_complete=flaky, today=TODAY)
        statuses = {r.task_id: r.status for r in report.results}
        assert statuses == {"boom-task": "anomaly", "t1": "applied"}
