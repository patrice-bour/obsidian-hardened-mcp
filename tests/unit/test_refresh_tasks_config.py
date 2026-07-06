"""Whitelist (`refresh_tasks:`) parsing and loading — vault-refresh v2."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from obsidian_hardened_mcp.domain.refresh import (
    ExecutorSettings,
    InvalidTaskError,
    RefreshTask,
    parse_refresh_task,
)
from obsidian_hardened_mcp.validation.config_loader import load_refresh_config

VALID = {
    "note": "01_Notes/target.md",
    "prompt": "Recount notes by type.",
    "tools": ["vault"],
}


class TestParseRefreshTask:
    def test_minimal_valid(self) -> None:
        t = parse_refresh_task("stats", VALID)
        assert t == RefreshTask(
            task_id="stats",
            note="01_Notes/target.md",
            prompt="Recount notes by type.",
            tools=frozenset({"vault"}),
            model=None,
            web_queries=(),
        )

    def test_tools_default_is_vault(self) -> None:
        t = parse_refresh_task("stats", {"note": "a.md", "prompt": "p"})
        assert t.tools == frozenset({"vault"})

    def test_web_requires_web_queries(self) -> None:
        raw = dict(VALID, tools=["vault", "web"])
        with pytest.raises(InvalidTaskError, match="web_queries"):
            parse_refresh_task("t", raw)

    def test_web_with_queries_ok(self) -> None:
        raw = dict(VALID, tools=["vault", "web"], web_queries=["q1", "q2"])
        t = parse_refresh_task("t", raw)
        assert t.web_queries == ("q1", "q2")

    def test_unknown_tool_rejected(self) -> None:
        with pytest.raises(InvalidTaskError, match="tools"):
            parse_refresh_task("t", dict(VALID, tools=["vault", "shell"]))

    @pytest.mark.parametrize("missing", ["note", "prompt"])
    def test_required_fields(self, missing: str) -> None:
        raw = {k: v for k, v in VALID.items() if k != missing}
        with pytest.raises(InvalidTaskError, match=missing):
            parse_refresh_task("t", raw)

    def test_empty_prompt_rejected(self) -> None:
        with pytest.raises(InvalidTaskError, match="prompt"):
            parse_refresh_task("t", dict(VALID, prompt="  "))

    @pytest.mark.parametrize("bad_tools", [5, "vault", {"vault": True}])
    def test_non_list_tools_rejected(self, bad_tools: object) -> None:
        with pytest.raises(InvalidTaskError, match="tools"):
            parse_refresh_task("t", dict(VALID, tools=bad_tools))

    @pytest.mark.parametrize("bad_queries", ["hello", 42, {"q": 1}])
    def test_non_list_web_queries_rejected(self, bad_queries: object) -> None:
        with pytest.raises(InvalidTaskError, match="web_queries"):
            parse_refresh_task("t", dict(VALID, web_queries=bad_queries))

    def test_note_dot_slash_prefix_normalized(self) -> None:
        # A `./`-prefixed note must pin identically to the un-prefixed form,
        # since the scan/apply sides never carry a `./` in `rel`.
        t = parse_refresh_task("t", dict(VALID, note="./01_Notes/target.md"))
        assert t.note == "01_Notes/target.md"

    def test_note_nfd_typed_stored_nfc_equal(self) -> None:
        # A note typed with an NFD-decomposed accented filename (e.g. copy-
        # pasted from a macOS/iCloud Finder path) must be stored NFC, so it
        # compares equal to the NFC relative path the scan/`refresh_apply`
        # sides derive from `VaultPath` (see `domain.vault_path.VaultPath`).
        nfc_name = "01_Notes/Paysage modèles.md"  # è as a single codepoint
        nfd_name = unicodedata.normalize("NFD", nfc_name)
        assert nfd_name != nfc_name  # sanity: the two forms really differ
        t = parse_refresh_task("t", dict(VALID, note=nfd_name))
        assert t.note == nfc_name
        assert unicodedata.is_normalized("NFC", t.note)


class TestLoadRefreshConfig:
    def test_missing_file_yields_empty(self, tmp_path: Path) -> None:
        tasks, settings, errors = load_refresh_config(tmp_path)
        assert tasks == {} and errors == []
        assert settings == ExecutorSettings()

    def test_full_load(self, tmp_path: Path) -> None:
        (tmp_path / ".obsidian-hardened-mcp.yaml").write_text(
            "refresh_tasks:\n"
            "  stats:\n"
            "    note: 01_Notes/target.md\n"
            "    prompt: Recount.\n"
            "  broken:\n"
            "    note: x.md\n"
            "refresh_executor:\n"
            "  max_usd_per_cycle: 1.25\n"
            "  local_routes: [local-thinker]\n"
        )
        tasks, settings, errors = load_refresh_config(tmp_path)
        assert set(tasks) == {"stats"}
        assert tasks["stats"].note == "01_Notes/target.md"
        assert settings.max_usd_per_cycle == 1.25
        assert settings.local_routes == ("local-thinker",)
        assert settings.min_body_ratio == 0.3
        assert len(errors) == 1 and "broken" in errors[0] and "prompt" in errors[0]

    def test_scalar_tools_entry_does_not_kill_load(self, tmp_path: Path) -> None:
        (tmp_path / ".obsidian-hardened-mcp.yaml").write_text(
            "refresh_tasks:\n"
            "  good:\n"
            "    note: 01_Notes/target.md\n"
            "    prompt: Recount.\n"
            "  bad:\n"
            "    note: x.md\n"
            "    prompt: p.\n"
            "    tools: 5\n"
        )
        tasks, settings, errors = load_refresh_config(tmp_path)
        assert set(tasks) == {"good"}
        assert settings == ExecutorSettings()
        assert len(errors) == 1 and "bad" in errors[0] and "tools" in errors[0]
