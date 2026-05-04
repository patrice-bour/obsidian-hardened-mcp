"""Integration tests for the CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_power_mcp.__main__ import main


def test_main_without_vault_exits_with_error_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["obsidian-power-mcp"])
    monkeypatch.delenv("OBSIDIAN_VAULT_ROOT", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
    assert "vault root is required" in capsys.readouterr().err


def test_main_with_valid_vault_constructs_server(
    monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
) -> None:
    """We don't exercise the full stdio loop here — just verify that argument
    parsing builds an `AppConfig` and instantiates the server before `run()`
    would block on stdio."""
    monkeypatch.setattr(sys, "argv", ["obsidian-power-mcp", "--vault", str(tmp_vault)])
    with patch("obsidian_power_mcp.server.FastMCP.run") as fake_run:
        main()
    fake_run.assert_called_once()


def test_main_accepts_max_file_size_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "obsidian-power-mcp",
            "--vault",
            str(tmp_vault),
            "--max-file-size-mb",
            "25",
        ],
    )
    captured: dict[str, object] = {}

    def fake_create_server(cfg: object) -> object:
        captured["cfg"] = cfg
        return type("Stub", (), {"run": lambda self: None})()

    with patch("obsidian_power_mcp.__main__.create_server", side_effect=fake_create_server):
        main()
    assert captured["cfg"].max_file_size_mb == 25  # type: ignore[attr-defined]
