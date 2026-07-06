# SPDX-License-Identifier: Apache-2.0
"""Dashboard report note — one entry per `run_cycle` call.

`append_report` writes into `01_Notes/_dashboards/Maj automatiques.md`
through the SAME server write tools the rest of the executor uses
(`create_note` when the note doesn't exist yet, `append_to_note`
afterwards) — it never touches the vault by any other means, mirroring
`core.py`'s own invariant for note bodies.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.write import append_to_note, create_note
from obsidian_hardened_mcp.validation.config_loader import load_validation_config

from refresh_executor.core import CycleReport, TaskResult

REPORT_PATH = "01_Notes/_dashboards/Maj automatiques.md"
"""Vault-relative path of the dashboard note the executor appends to."""

_APPLIED_ICON = "✅"  # white heavy check mark
_OTHER_ICON = "⚠"  # warning sign


def append_report(vault_root: Path, report: CycleReport, when: datetime) -> None:
    """Append one `## <when>` entry summarising `report` to the dashboard note.

    Builds its own `AppConfig`/`AuditLogger`/hooks the same way `run_cycle`
    does (so the write is subject to the SAME vault validation hooks as
    every other write path), then creates the note on first use and
    appends to it on every subsequent call. Raises `RuntimeError` if the
    underlying server tool call fails, so a broken write never passes for
    a successful one.
    """
    config = AppConfig.from_env(vault_root)
    audit = AuditLogger(audit_dir=config.audit_dir)
    hooks = load_validation_config(config.vault_root)

    entry = _format_entry(report, when)

    if (config.vault_root / REPORT_PATH).exists():
        result = append_to_note(config, audit, REPORT_PATH, entry, hooks=hooks)
    else:
        result = create_note(config, audit, REPORT_PATH, entry, hooks=hooks)

    if not result.ok:
        reason = result.error.message if result.error else "unknown error"
        raise RuntimeError(f"append_report failed writing {REPORT_PATH}: {reason}")


def _format_entry(report: CycleReport, when: datetime) -> str:
    lines = [f"## {when:%Y-%m-%d %H:%M}"]
    lines.extend(_format_line(result) for result in report.results)
    return "\n".join(lines) + "\n"


def _format_line(result: TaskResult) -> str:
    icon = _APPLIED_ICON if result.status == "applied" else _OTHER_ICON
    line = f"- {icon} {result.task_id} ({result.path}) — {result.model} — ${result.cost:.4f}"
    if result.reason:
        line += f" — {result.reason}"
    return line
