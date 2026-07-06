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

    def test_cloud_model_without_cloud_tool_is_anomaly(
        self, exec_vault_cloud_denied: Path
    ) -> None:
        report = run_cycle(exec_vault_cloud_denied, llm_complete=fake_llm, today=TODAY)
        [res] = report.results
        assert res.status == "anomaly"
        assert res.reason == "cloud route not allowed"
        assert res.cost == 0.0

    def test_cost_cap_stops_cloud_tasks_but_not_vault_only(
        self, exec_vault_cost_cap: Path
    ) -> None:
        def costly_llm(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
            return "# Refreshed\n\nNew generated body, long enough to pass ratio.\n", 0.02

        report = run_cycle(exec_vault_cost_cap, llm_complete=costly_llm, today=TODAY)
        by_id = {r.task_id: r for r in report.results}

        cloud_statuses = sorted([by_id["cloud1"].status, by_id["cloud2"].status])
        assert cloud_statuses == ["anomaly", "applied"]
        cap_anomaly = next(
            by_id[tid] for tid in ("cloud1", "cloud2") if by_id[tid].status == "anomaly"
        )
        assert cap_anomaly.reason == "cost cap reached"
        assert cap_anomaly.cost == 0.0

        assert by_id["vault1"].status == "applied"

    def test_cloud_tool_with_local_route_survives_cost_cap(
        self, exec_vault_cap_hybrid: Path
    ) -> None:
        """`hybrid1` declares the `cloud` tool but resolves to a LOCAL route
        (no `model` override) — its calls cannot bill, so the cap must never
        stop it. The fixture guarantees it runs AFTER the cloud tasks have
        exceeded the cap (subdirectory ordering), the very case where a cap
        keyed on declared permission (instead of resolved route) fails."""
        def route_priced_llm(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
            cost = 0.02 if route == "cloud-x" else 0.0
            return "# Refreshed\n\nNew generated body, long enough to pass ratio.\n", cost

        report = run_cycle(exec_vault_cap_hybrid, llm_complete=route_priced_llm, today=TODAY)
        by_id = {r.task_id: r for r in report.results}

        # Cap engaged: one cloud task applied, the other stopped.
        cloud_statuses = sorted([by_id["cloud1"].status, by_id["cloud2"].status])
        assert cloud_statuses == ["anomaly", "applied"]

        # The hybrid task ran after the cap was exceeded, on a local route:
        # zero-cost, so it continues.
        assert by_id["hybrid1"].status == "applied"
        assert by_id["hybrid1"].model == "local-thinker"
        assert by_id["hybrid1"].cost == 0.0

    def test_broken_whitelist_entry_surfaces_as_anomaly(
        self, exec_vault_broken_whitelist: Path
    ) -> None:
        """A whitelist entry that fails to parse (missing `prompt`) must
        not be silently dropped: it should show up as an anomaly
        TaskResult alongside the other, unrelated task still running."""
        report = run_cycle(exec_vault_broken_whitelist, llm_complete=fake_llm, today=TODAY)

        anomalies = [r for r in report.results if r.task_id == "<config>"]
        assert len(anomalies) == 1
        assert anomalies[0].status == "anomaly"
        assert "broken-task" in anomalies[0].reason
        assert "prompt" in anomalies[0].reason

        [t1_result] = [r for r in report.results if r.task_id == "t1"]
        assert t1_result.status == "applied"

    def test_only_task_filters_before_any_llm_call(self, exec_vault_two_tasks: Path) -> None:
        """`only_task` must skip the non-matching task entirely — never even
        reaching the LLM — not merely omit it from the report after a call."""
        calls: list[str] = []

        def counting_llm(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
            calls.append(route)
            return fake_llm(route, messages)

        report = run_cycle(
            exec_vault_two_tasks, llm_complete=counting_llm, today=TODAY, only_task="t1"
        )

        assert [r.task_id for r in report.results] == ["t1"]
        assert calls == ["local-thinker"]
        boom_text = (exec_vault_two_tasks / "01_Notes" / "boom.md").read_text()
        assert "New generated body" not in boom_text


class TestRunCycleWeb:
    def test_web_search_emits_only_declared_queries(self, exec_vault_web: Path) -> None:
        """Security invariant: only the task's DECLARED `web_queries` are
        ever searched — nothing derived from note content or LLM output."""
        recorded: list[str] = []

        def fake_web_search(query: str) -> str:
            recorded.append(query)
            return f"[result for {query}]"

        captured: dict[str, str] = {}

        def capturing_llm(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
            captured["content"] = messages[-1]["content"]
            return fake_llm(route, messages)

        report = run_cycle(
            exec_vault_web,
            llm_complete=capturing_llm,
            web_search=fake_web_search,
            today=TODAY,
        )

        assert recorded == ["first query", "second query"]
        [res] = report.results
        assert res.status == "applied"
        assert "[result for first query]" in captured["content"]
        assert "[result for second query]" in captured["content"]

    def test_web_declared_without_web_search_is_anomaly(self, exec_vault_web: Path) -> None:
        report = run_cycle(exec_vault_web, llm_complete=fake_llm, today=TODAY)
        [res] = report.results
        assert res.status == "anomaly"
        assert res.reason == "web unavailable"
        assert res.cost == 0.0
