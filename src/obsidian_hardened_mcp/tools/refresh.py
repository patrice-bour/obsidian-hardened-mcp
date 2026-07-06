# SPDX-License-Identifier: Apache-2.0
"""vault-refresh tool — `list_stale_notes`.

Deterministic scan of the vault's `refresh_*` contracts (see
docs/superpowers/specs/2026-07-06-vault-refresh-design.md). Read-only by
default; `mark=True` stamps the two derived fields (`refresh_due`,
`refresh_stale`) through the round-trip-aware frontmatter layer, so those
writes are atomic and audited like any other frontmatter write.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.refresh import (
    POLICIES,
    InvalidContractError,
    compute_due,
    parse_contract,
)
from obsidian_hardened_mcp.domain.results import ErrorCode, ToolResult
from obsidian_hardened_mcp.domain.vault_path import VaultPath
from obsidian_hardened_mcp.frontmatter import parse_note
from obsidian_hardened_mcp.fs.listing import iter_markdown
from obsidian_hardened_mcp.fs.reader import read_text
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools._base import tool_call


@tool_call
def list_stale_notes(
    config: AppConfig,
    audit: AuditLogger,
    *,
    mark: bool = False,
    policy: str | None = None,
    today: dt.date | None = None,
) -> ToolResult:
    """Scan the vault for notes whose refresh contract is overdue.

    A note is under contract when its frontmatter carries `refresh_every`
    AND `refresh_last`. Never touches note bodies. `today` is injectable
    for tests; defaults to the local date.
    """
    if policy is not None and policy not in POLICIES:
        return ToolResult.failure(
            ErrorCode.VALIDATION_FAILED,
            f"unknown policy: {policy!r} (expected one of {POLICIES})",
        )
    if today is None:
        today = dt.date.today()

    scanned = with_contract = marked = 0
    stale: list[dict[str, Any]] = []
    anomalies: list[dict[str, str]] = []

    for abs_path in iter_markdown(config.vault_root):
        rel = abs_path.relative_to(config.vault_root).as_posix()
        scanned += 1
        try:
            vp = VaultPath.from_user(rel, config.vault_root)
            text = read_text(vp, max_size_bytes=config.max_file_size_bytes)
            contract = parse_contract(parse_note(text).frontmatter)
        except InvalidContractError as exc:
            anomalies.append({"path": rel, "reason": str(exc)})
            continue
        except Exception as exc:  # unreadable/malformed note: report, keep going
            anomalies.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if contract is None:
            continue
        with_contract += 1
        if policy is not None and contract.policy != policy:
            continue
        due = compute_due(contract.last, contract.every)
        is_stale = today >= due
        if mark:
            marked += _mark_note(config, audit, rel, due=due, stale=is_stale)
        if is_stale:
            stale.append(
                {
                    "path": rel,
                    "policy": contract.policy,
                    "last": contract.last.isoformat(),
                    "due": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "prompt": contract.prompt,
                }
            )

    return ToolResult.success(
        data={
            "scanned": scanned,
            "with_contract": with_contract,
            "marked": marked,
            "stale": stale,
            "anomalies": anomalies,
        }
    )


def _mark_note(
    config: AppConfig,
    audit: AuditLogger,
    rel: str,
    *,
    due: dt.date,
    stale: bool,
) -> int:
    """Stamp `refresh_due`/`refresh_stale` when they differ from the stored
    values. Returns 1 when a write happened, 0 otherwise. Delegates to
    `merge_frontmatter` so the write is atomic, round-trip-safe and audited."""
    from obsidian_hardened_mcp.tools.frontmatter import merge_frontmatter

    vp = VaultPath.from_user(rel, config.vault_root)
    text = read_text(vp, max_size_bytes=config.max_file_size_bytes)
    fm: dict[str, Any] = parse_note(text).frontmatter or {}
    current_due = fm.get("refresh_due")
    if isinstance(current_due, dt.date):
        current_due = current_due.isoformat()
    if str(current_due) == due.isoformat() and fm.get("refresh_stale") is stale:
        return 0
    result = merge_frontmatter(
        config,
        audit,
        rel,
        {"refresh_due": due.isoformat(), "refresh_stale": stale},
        mode="shallow",
    )
    return 1 if result.ok else 0
