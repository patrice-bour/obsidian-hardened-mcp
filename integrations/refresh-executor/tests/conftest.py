"""Shared pytest fixtures for refresh-executor tests.

`tmp_vault` copies the server's `tests/conftest.py` idiom (a throwaway
vault directory under `tmp_path`, forbidden zones included so the scan
logic exercises the same layout it does against the server). The
`exec_vault*` fixtures layer a `.obsidian-hardened-mcp.yaml` refresh-tasks
whitelist plus one or two already-stale `auto`-policy notes on top, and
redirect `OBSIDIAN_AUDIT_DIR` into `tmp_path` (the same isolation pattern
`tests/e2e/run_e2e.py` uses for the server) so `run_cycle` never touches
the real `~/.obsidian-hardened-mcp/audit/`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Iterator[Path]:
    """Provide a temporary vault root with the standard forbidden-zone layout."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "config.json").write_text("{}")
    (root / ".git").mkdir()
    (root / ".trash").mkdir()
    (root / ".ohmcp-trash").mkdir()
    (root / "01_Notes").mkdir()
    yield root


@pytest.fixture(autouse=True)
def _isolated_audit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the server-default audit dir into `tmp_path` for every test.

    `run_cycle` builds its `AppConfig` via `AppConfig.from_env(vault_root)`
    — the same call the server's own entry point makes — so it honours
    `OBSIDIAN_AUDIT_DIR` when set. Without this, tests would write real
    JSONL audit files under the invoking user's home directory.
    """
    monkeypatch.setenv("OBSIDIAN_AUDIT_DIR", str(tmp_path / "audit"))


# A previous body long enough that a 1-char LLM reply trips the
# `min_body_ratio` (default 0.3) output guard, while the `fake_llm` /
# `flaky` bodies used in test_core.py (~60+ chars) comfortably pass it.
_STALE_BODY = "Old body, stale content that has needed a refresh for a while now.\n"


@pytest.fixture
def exec_vault(tmp_vault: Path) -> Path:
    """A vault with one executable `auto` task (`t1`) on one stale note."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_tasks:\n"
        "  t1:\n"
        "    note: 01_Notes/auto.md\n"
        "    prompt: Refresh this note with the latest summary.\n"
    )
    (tmp_vault / "01_Notes" / "auto.md").write_text(
        "---\n"
        "title: Auto note\n"
        "refresh_policy: auto\n"
        "refresh_task: t1\n"
        "refresh_every: 1m\n"
        "refresh_last: 2026-05-01\n"
        "---\n" + _STALE_BODY
    )
    return tmp_vault


@pytest.fixture
def exec_vault_cloud_denied(tmp_vault: Path) -> Path:
    """A vault with one `auto` task pinned to a cloud model but WITHOUT the
    `cloud` tool — the route guard must refuse it before any LLM call."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_tasks:\n"
        "  t1:\n"
        "    note: 01_Notes/auto.md\n"
        "    prompt: Refresh this note with the latest summary.\n"
        "    model: cloud-x\n"
    )
    (tmp_vault / "01_Notes" / "auto.md").write_text(
        "---\n"
        "refresh_policy: auto\n"
        "refresh_task: t1\n"
        "refresh_every: 1m\n"
        "refresh_last: 2026-05-01\n"
        "---\n" + _STALE_BODY
    )
    return tmp_vault


@pytest.fixture
def exec_vault_cost_cap(tmp_vault: Path) -> Path:
    """A vault with two `cloud`-tooled tasks (`cloud1`, `cloud2`) and one
    vault-only task (`vault1`), and a `max_usd_per_cycle` of 0.01 — tight
    enough that a fake LLM costing 0.02/call trips the cap after the first
    cloud task, while `vault1` must still run past the cap."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_executor:\n"
        "  max_usd_per_cycle: 0.01\n"
        "  local_routes:\n"
        "    - local-thinker\n"
        "refresh_tasks:\n"
        "  cloud1:\n"
        "    note: 01_Notes/cloud1.md\n"
        "    prompt: Refresh this note via the cloud model.\n"
        "    tools: [cloud]\n"
        "    model: cloud-x\n"
        "  cloud2:\n"
        "    note: 01_Notes/cloud2.md\n"
        "    prompt: Refresh this other note via the cloud model.\n"
        "    tools: [cloud]\n"
        "    model: cloud-x\n"
        "  vault1:\n"
        "    note: 01_Notes/vault1.md\n"
        "    prompt: Refresh this note with only vault access.\n"
    )
    for name, task_id in (("cloud1", "cloud1"), ("cloud2", "cloud2"), ("vault1", "vault1")):
        (tmp_vault / "01_Notes" / f"{name}.md").write_text(
            "---\n"
            "refresh_policy: auto\n"
            f"refresh_task: {task_id}\n"
            "refresh_every: 1m\n"
            "refresh_last: 2026-05-01\n"
            "---\n" + _STALE_BODY
        )
    return tmp_vault


@pytest.fixture
def exec_vault_cap_hybrid(tmp_vault: Path) -> Path:
    """Like `exec_vault_cost_cap`, plus a `hybrid1` task that DECLARES the
    `cloud` tool but has no `model` override — its resolved route is local,
    so its calls cannot bill and the cost cap must never stop it.

    `hybrid.md` lives in a SUBDIRECTORY of `01_Notes/`: `iter_markdown`
    yields a directory's own files before any of its subdirectories' files
    (subdirs are stacked and popped after the current directory finishes),
    so `hybrid1` is guaranteed to run AFTER the cloud tasks have already
    blown past the cap — the exact ordering that exposes a cap check keyed
    on declared permission instead of resolved route."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_executor:\n"
        "  max_usd_per_cycle: 0.01\n"
        "  local_routes:\n"
        "    - local-thinker\n"
        "refresh_tasks:\n"
        "  cloud1:\n"
        "    note: 01_Notes/cloud1.md\n"
        "    prompt: Refresh this note via the cloud model.\n"
        "    tools: [cloud]\n"
        "    model: cloud-x\n"
        "  cloud2:\n"
        "    note: 01_Notes/cloud2.md\n"
        "    prompt: Refresh this other note via the cloud model.\n"
        "    tools: [cloud]\n"
        "    model: cloud-x\n"
        "  hybrid1:\n"
        "    note: 01_Notes/deep/hybrid.md\n"
        "    prompt: Refresh this note; cloud allowed but route stays local.\n"
        "    tools: [vault, cloud]\n"
    )
    (tmp_vault / "01_Notes" / "deep").mkdir()
    for rel, task_id in (
        ("cloud1.md", "cloud1"),
        ("cloud2.md", "cloud2"),
        ("deep/hybrid.md", "hybrid1"),
    ):
        (tmp_vault / "01_Notes" / rel).write_text(
            "---\n"
            "refresh_policy: auto\n"
            f"refresh_task: {task_id}\n"
            "refresh_every: 1m\n"
            "refresh_last: 2026-05-01\n"
            "---\n" + _STALE_BODY
        )
    return tmp_vault


@pytest.fixture
def exec_vault_web(tmp_vault: Path) -> Path:
    """A vault with one `auto` task declaring the `web` tool and two
    `web_queries` — used to assert only the DECLARED queries are ever
    searched, and that a missing `web_search` (no API key) is an anomaly."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_tasks:\n"
        "  web1:\n"
        "    note: 01_Notes/auto.md\n"
        "    prompt: Refresh this note with the latest summary.\n"
        "    tools: [web]\n"
        "    web_queries:\n"
        "      - first query\n"
        "      - second query\n"
    )
    (tmp_vault / "01_Notes" / "auto.md").write_text(
        "---\n"
        "refresh_policy: auto\n"
        "refresh_task: web1\n"
        "refresh_every: 1m\n"
        "refresh_last: 2026-05-01\n"
        "---\n" + _STALE_BODY
    )
    return tmp_vault


@pytest.fixture
def exec_vault_broken_whitelist(tmp_vault: Path) -> Path:
    """A vault whose whitelist has one valid task (`t1`) and one BROKEN
    entry (`broken-task`, missing the required `prompt`) — `run_cycle`
    must still run `t1` to completion AND surface `broken-task` as an
    anomaly TaskResult (Finding 1: scan/config anomalies must not be
    silently dropped)."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_tasks:\n"
        "  t1:\n"
        "    note: 01_Notes/auto.md\n"
        "    prompt: Refresh this note with the latest summary.\n"
        "  broken-task:\n"
        "    note: 01_Notes/other.md\n"
    )
    (tmp_vault / "01_Notes" / "auto.md").write_text(
        "---\n"
        "refresh_policy: auto\n"
        "refresh_task: t1\n"
        "refresh_every: 1m\n"
        "refresh_last: 2026-05-01\n"
        "---\n" + _STALE_BODY
    )
    return tmp_vault


@pytest.fixture
def exec_vault_two_tasks(tmp_vault: Path) -> Path:
    """A vault with two executable tasks: `t1` (fine) and `boom-task`
    (whose prompt contains the word "boom", so a flaky `llm_complete`
    stub can single it out and fail only that one task)."""
    (tmp_vault / ".obsidian-hardened-mcp.yaml").write_text(
        "refresh_tasks:\n"
        "  t1:\n"
        "    note: 01_Notes/auto.md\n"
        "    prompt: Refresh this note with the latest summary.\n"
        "  boom-task:\n"
        "    note: 01_Notes/boom.md\n"
        "    prompt: Please boom this note with a fresh summary.\n"
    )
    (tmp_vault / "01_Notes" / "auto.md").write_text(
        "---\n"
        "refresh_policy: auto\n"
        "refresh_task: t1\n"
        "refresh_every: 1m\n"
        "refresh_last: 2026-05-01\n"
        "---\n" + _STALE_BODY
    )
    (tmp_vault / "01_Notes" / "boom.md").write_text(
        "---\n"
        "refresh_policy: auto\n"
        "refresh_task: boom-task\n"
        "refresh_every: 1m\n"
        "refresh_last: 2026-05-01\n"
        "---\n" + _STALE_BODY
    )
    return tmp_vault
