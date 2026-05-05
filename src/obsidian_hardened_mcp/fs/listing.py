# SPDX-License-Identifier: Apache-2.0
"""Markdown file enumeration with forbidden-zone pruning.

Used by `tools.read.list_notes` and `tools.meta.get_vault_info`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

# Mirror of `_FORBIDDEN_DIR_PREFIXES` in `domain.vault_path`. Kept here as a
# separate constant to avoid an import cycle (vault_path is a leaf module).
_PRUNED_DIRS: frozenset[str] = frozenset(
    {".obsidian", ".git", ".trash", ".ohmcp-trash"}
)


def iter_markdown(root: Path) -> Iterator[Path]:
    """Yield every `.md` file under `root`, pruning forbidden directories.

    Symlinked directories ARE followed; the caller is expected to have
    constructed `root` via a `VaultPath`, which already rejects escaping
    symlinks. We do not re-validate here to keep the walker fast.
    """
    if not root.is_dir():
        return
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):  # pragma: no cover - defensive
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in _PRUNED_DIRS:
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix == ".md":
                yield entry
