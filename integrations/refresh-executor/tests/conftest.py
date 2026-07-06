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
