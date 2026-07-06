# SPDX-License-Identifier: Apache-2.0
"""Tests for the `refresh-executor` console entry point (`cli.main`).

`litellm_complete_factory` is monkeypatched at the `cli` module's own
reference so `main` never opens a real `httpx.Client` or hits a network
endpoint — the fake factory returns a canned `LlmComplete` long enough to
pass `core.py`'s output guard.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from refresh_executor import cli
from refresh_executor.report import REPORT_PATH

LlmFactory = Callable[[str, str], Callable[[str, list[dict[str, str]]], tuple[str, float]]]


def _fake_llm_factory(calls: list[str] | None = None) -> LlmFactory:
    def factory(
        base_url: str, api_key: str, **_kwargs: object
    ) -> Callable[[str, list[dict[str, str]]], tuple[str, float]]:
        def complete(route: str, messages: list[dict[str, str]]) -> tuple[str, float]:
            if calls is not None:
                calls.append(route)
            return "# Refreshed\n\nNew generated body, long enough to pass ratio.\n", 0.0

        return complete

    return factory


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's real LiteLLM/Tavily env leak into a CLI test."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_TIMEOUT_S", raising=False)


class TestMain:
    def test_dry_run_prints_report_and_writes_nothing(
        self,
        exec_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(cli, "litellm_complete_factory", _fake_llm_factory())

        exit_code = cli.main(["--vault", str(exec_vault), "--dry-run"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "t1" in out
        assert not (exec_vault / REPORT_PATH).exists()

    def test_normal_run_writes_report_note(
        self, exec_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "litellm_complete_factory", _fake_llm_factory())

        exit_code = cli.main(["--vault", str(exec_vault)])

        assert exit_code == 0
        report_text = (exec_vault / REPORT_PATH).read_text()
        assert re.search(r"## \d{4}-\d{2}-\d{2} \d{2}:\d{2}", report_text)
        assert "✅ t1 (01_Notes/auto.md)" in report_text
        assert "New generated body" in (exec_vault / "01_Notes" / "auto.md").read_text()

    def test_second_run_appends_to_existing_report(
        self, exec_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "litellm_complete_factory", _fake_llm_factory())
        cli.main(["--vault", str(exec_vault)])
        first = (exec_vault / REPORT_PATH).read_text()

        # Re-stale the note (refresh_apply stamped refresh_last=today) so the
        # second cycle has something to do again.
        note = exec_vault / "01_Notes" / "auto.md"
        pattern = r"refresh_last: \d{4}-\d{2}-\d{2}"
        note.write_text(re.sub(pattern, "refresh_last: 2020-01-01", note.read_text()))

        cli.main(["--vault", str(exec_vault)])
        second = (exec_vault / REPORT_PATH).read_text()

        assert second.startswith(first)
        assert second.count("## ") == 2

    def test_fatal_error_on_missing_vault_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "litellm_complete_factory", _fake_llm_factory())
        missing = tmp_path / "does-not-exist"

        exit_code = cli.main(["--vault", str(missing)])

        assert exit_code == 1

    def test_anomaly_still_exits_0_and_is_reported(
        self, exec_vault_cloud_denied: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "litellm_complete_factory", _fake_llm_factory())

        exit_code = cli.main(["--vault", str(exec_vault_cloud_denied)])

        assert exit_code == 0
        report_text = (exec_vault_cloud_denied / REPORT_PATH).read_text()
        assert "⚠ t1" in report_text
        assert "cloud route not allowed" in report_text

    def test_litellm_timeout_s_env_var_threaded_to_factory(
        self, exec_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def factory(
            base_url: str, api_key: str, **kwargs: object
        ) -> Callable[[str, list[dict[str, str]]], tuple[str, float]]:
            captured.update(kwargs)
            return _fake_llm_factory()(base_url, api_key)

        monkeypatch.setattr(cli, "litellm_complete_factory", factory)
        monkeypatch.setenv("LITELLM_TIMEOUT_S", "7.5")

        exit_code = cli.main(["--vault", str(exec_vault), "--dry-run"])

        assert exit_code == 0
        assert captured["timeout_s"] == 7.5

    def test_task_filter_skips_other_tasks_without_calling_llm(
        self, exec_vault_two_tasks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(cli, "litellm_complete_factory", _fake_llm_factory(calls))

        exit_code = cli.main(["--vault", str(exec_vault_two_tasks), "--task", "t1"])

        assert exit_code == 0
        report_text = (exec_vault_two_tasks / REPORT_PATH).read_text()
        assert "t1" in report_text
        assert "boom-task" not in report_text
        boom_text = (exec_vault_two_tasks / "01_Notes" / "boom.md").read_text()
        assert "New generated body" not in boom_text
