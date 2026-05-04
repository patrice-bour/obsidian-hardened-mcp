"""Shared pytest fixtures for obsidian-power-mcp tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Iterator[Path]:
    """Provide a temporary vault root with the standard layout.

    Layout:
        <root>/
        ├── .obsidian/             # forbidden zone
        │   └── config.json
        ├── .git/                  # forbidden zone
        ├── .trash/                # forbidden zone
        ├── .opmcp-trash/          # forbidden zone
        ├── .obsidian-power-mcp.yaml   # forbidden file
        ├── 00_Journal/
        │   └── 2026-05-04.md
        ├── 01_Notes/
        │   └── sample.md
        └── _VAULT.md
    """
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "config.json").write_text("{}")
    (root / ".git").mkdir()
    (root / ".trash").mkdir()
    (root / ".opmcp-trash").mkdir()
    (root / ".obsidian-power-mcp.yaml").write_text("schemas: {}\n")
    (root / "00_Journal").mkdir()
    (root / "00_Journal" / "2026-05-04.md").write_text("# Today\n")
    (root / "01_Notes").mkdir()
    (root / "01_Notes" / "sample.md").write_text("# Sample\n")
    (root / "_VAULT.md").write_text("# Vault root\n")
    yield root
