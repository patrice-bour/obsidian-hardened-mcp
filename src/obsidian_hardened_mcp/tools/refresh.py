# SPDX-License-Identifier: Apache-2.0
"""vault-refresh tools — `list_stale_notes`, `refresh_apply`.

Deterministic scan of the vault's `refresh_*` contracts (vault-refresh v1
design, 2026-07-06). Read-only by default; `mark=True` stamps the two
derived fields (`refresh_due`, `refresh_stale`) through the round-trip-aware
frontmatter layer, so those writes are atomic and audited like any other
frontmatter write.

`refresh_apply` (vault-refresh v2) is the SOLE write path for the automated
executor: it snapshots the note to `.ohmcp-trash/` before mutating it, then
replaces the body while stamping the server-managed contract fields
(`refresh_last`, `refresh_due`, `refresh_stale`). It only executes for notes
whose `auto` contract is pinned to exactly this path in the vault's
`refresh_tasks:` whitelist — the same resolution rule as `_resolve_auto`.
"""

from __future__ import annotations

import copy
import datetime as dt
import time
from collections.abc import Mapping
from typing import Any

from ruamel.yaml.comments import CommentedMap

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.refresh import (
    POLICIES,
    InvalidContractError,
    RefreshContract,
    RefreshTask,
    compute_due,
    parse_contract,
)
from obsidian_hardened_mcp.domain.results import ErrorCode, ToolResult
from obsidian_hardened_mcp.domain.vault_path import VaultPath
from obsidian_hardened_mcp.frontmatter import ParsedNote, parse_note, render_note
from obsidian_hardened_mcp.fs.listing import iter_markdown
from obsidian_hardened_mcp.fs.reader import read_text
from obsidian_hardened_mcp.fs.snapshot import SnapshotError, snapshot_for_destruction
from obsidian_hardened_mcp.fs.writer import atomic_write_text
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools._base import (
    emit_audit,
    map_exception,
    new_request_id,
    params_hash,
    run_validation_hooks,
    to_plain_dict,
    tool_call,
)
from obsidian_hardened_mcp.tools.frontmatter import merge_frontmatter
from obsidian_hardened_mcp.validation.config_loader import (
    CONFIG_FILE_NAME,
    load_refresh_config,
)
from obsidian_hardened_mcp.validation.hooks import HookContext, HookRegistry


@tool_call
def list_stale_notes(
    config: AppConfig,
    audit: AuditLogger,
    *,
    mark: bool = False,
    policy: str | None = None,
    today: dt.date | None = None,
    hooks: HookRegistry | None = None,
) -> ToolResult:
    """Scan the vault for notes whose refresh contract is overdue.

    A note is under contract when its frontmatter carries `refresh_every`
    AND `refresh_last`. Never touches note bodies. `today` is injectable
    for tests; defaults to the local date. When `policy` is given, only
    matching notes are marked and reported; `with_contract` still counts
    all contracted notes.
    """
    if policy is not None and policy not in POLICIES:
        return ToolResult.failure(
            ErrorCode.VALIDATION_FAILED,
            f"unknown policy: {policy!r} (expected one of {POLICIES})",
        )
    if not config.vault_root.is_dir():
        return ToolResult.failure(
            ErrorCode.NOT_FOUND,
            "vault root unavailable: " + str(config.vault_root),
        )
    if today is None:
        today = dt.date.today()

    scanned = with_contract = marked = 0
    stale: list[dict[str, Any]] = []
    anomalies: list[dict[str, str]] = []

    tasks, _settings, cfg_errors = load_refresh_config(config.vault_root)
    for message in cfg_errors:
        anomalies.append({"path": CONFIG_FILE_NAME, "reason": message})

    for abs_path in iter_markdown(config.vault_root):
        rel = abs_path.relative_to(config.vault_root).as_posix()
        scanned += 1
        try:
            vp = VaultPath.from_user(rel, config.vault_root)
            # `VaultPath.from_user` NFC-normalises the relative path; reuse
            # that canonical form (rather than the raw, possibly NFD, on-disk
            # posix path) for every downstream comparison and report field.
            # This keeps the scan side in lockstep with `refresh_apply`'s
            # `rel = str(vp.relative)` and the whitelist's normalized `note:`
            # (`domain.refresh.parse_refresh_task`) — one normalization idiom,
            # not three independently-drifting ones.
            rel = str(vp.relative)
            text = read_text(vp, max_size_bytes=config.max_file_size_bytes)
            parsed = parse_note(text)
            contract = parse_contract(parsed.frontmatter)
            if contract is None:
                continue
            due = compute_due(contract.last, contract.every)
        except InvalidContractError as exc:
            anomalies.append({"path": rel, "reason": str(exc)})
            continue
        except Exception as exc:  # unreadable/malformed note: report, keep going
            anomalies.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        with_contract += 1
        if policy is not None and contract.policy != policy:
            continue
        is_stale = today >= due
        if mark:
            marked += _mark_note(
                config,
                audit,
                rel,
                frontmatter=parsed.frontmatter,
                due=due,
                stale=is_stale,
                anomalies=anomalies,
                hooks=hooks,
            )
        if is_stale:
            task_id, executable = _resolve_auto(
                contract, rel, parsed.frontmatter, tasks, anomalies
            )
            stale.append(
                {
                    "path": rel,
                    "policy": contract.policy,
                    "last": contract.last.isoformat(),
                    "due": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "prompt": contract.prompt,
                    "task": task_id,
                    "executable": executable,
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


def _resolve_auto(
    contract: RefreshContract,
    rel: str,
    fm: Mapping[str, Any] | None,
    tasks: Mapping[str, RefreshTask],
    anomalies: list[dict[str, str]],
) -> tuple[str | None, bool]:
    """Resolve an `auto`-policy note's `refresh_task` against the vault's
    whitelist (Task 1's `load_refresh_config`).

    The whitelist is the ONLY source of executable prompts: a task is
    executable only when it exists in `tasks` AND its declared `note` is
    pinned to exactly this note's path (`rel`). Any mismatch is reported as
    an anomaly and the note is still listed as `stale` (policy `flag`
    treatment), just never executable. Notes whose policy isn't `auto`
    always return `(None, False)`.
    """
    if contract.policy != "auto":
        return None, False

    refresh_task = (fm or {}).get("refresh_task")
    if refresh_task is None:
        anomalies.append(
            {"path": rel, "reason": "missing refresh_task (required for policy: auto)"}
        )
        return None, False

    task_id = str(refresh_task)
    task = tasks.get(task_id)
    if task is None:
        anomalies.append(
            {"path": rel, "reason": f"unknown refresh_task: {task_id!r}"}
        )
        return None, False

    if task.note != rel:
        anomalies.append(
            {
                "path": rel,
                "reason": (
                    f"task/note mismatch: task {task_id!r} is pinned to "
                    f"{task.note!r}, not {rel!r}"
                ),
            }
        )
        return None, False

    return task_id, True


def _mark_note(
    config: AppConfig,
    audit: AuditLogger,
    rel: str,
    *,
    frontmatter: Mapping[str, Any] | None,
    due: dt.date,
    stale: bool,
    anomalies: list[dict[str, str]],
    hooks: HookRegistry | None = None,
) -> int:
    """Stamp `refresh_due`/`refresh_stale` when they differ from the stored
    values. Returns 1 when a write happened, 0 otherwise. Delegates to
    `merge_frontmatter` so the write is atomic, round-trip-safe and audited.

    `frontmatter` is the mapping the caller already parsed during the scan
    (single read per note): no second read/parse happens here. A file that
    vanishes between the scan and the write surfaces as a NOT_FOUND anomaly
    via `merge_frontmatter`'s own guarded read, never an aborted scan.

    A failed write (e.g. rejected by a validation hook) is recorded into
    `anomalies` rather than silently counted as a no-op."""
    current = to_plain_dict(dict(frontmatter)) if frontmatter else {}
    if current.get("refresh_due") == due.isoformat() and current.get("refresh_stale") is stale:
        return 0
    result = merge_frontmatter(
        config,
        audit,
        rel,
        {"refresh_due": due.isoformat(), "refresh_stale": stale},
        mode="shallow",
        hooks=hooks,
    )
    if not result.ok:
        anomalies.append(
            {
                "path": rel,
                "reason": (
                    f"mark failed: {result.error.code.value if result.error else 'unknown'}"
                ),
            }
        )
        return 0
    return 1


# ---------------------------------------------------------------------------
# refresh_apply (vault-refresh v2) — the sole auto-write path
# ---------------------------------------------------------------------------

_TRASH_DIRNAME = ".ohmcp-trash"


def refresh_apply(
    config: AppConfig,
    audit: AuditLogger,
    path: str,
    body: str,
    *,
    hooks: HookRegistry | None = None,
    today: dt.date | None = None,
) -> ToolResult:
    """Sole write path for auto refresh tasks: snapshot, body-only replace,
    server-managed contract fields. Single-phase (automation), audited.

    Preconditions, checked before any write-side effect: the note must carry
    a valid refresh contract, its policy must be `auto`, and its declared
    `refresh_task` must be pinned to exactly this path in the vault's
    `refresh_tasks:` whitelist (the same rule `_resolve_auto` applies during
    scans). Any mismatch is refused as `VALIDATION_FAILED` with zero side
    effects — no snapshot, no write.

    Validation hooks run BEFORE the snapshot (a rejection must leave the
    vault untouched); the snapshot is taken BEFORE any write (idiom:
    `fs.snapshot.snapshot_for_destruction`, as used by the destructive
    tools). No `@tool_call` decorator: audit emission is handled with
    fine-grained control (snapshot_id, failure paths) in the try/except
    below, mirroring `tools/destructive.py`.
    """
    request_id = new_request_id()
    tool_name = "refresh_apply"
    started = time.monotonic()
    if today is None:
        today = dt.date.today()

    vp: VaultPath | None = None
    snapshot_id: str | None = None
    payload_hash_value = params_hash(path, len(body))
    try:
        vp = VaultPath.from_user(path, config.vault_root)
        text = read_text(vp, max_size_bytes=config.max_file_size_bytes)
        parsed = parse_note(text)
        fm = parsed.frontmatter
        rel = str(vp.relative)

        try:
            contract = parse_contract(fm)
        except InvalidContractError as exc:
            return ToolResult.failure(ErrorCode.VALIDATION_FAILED, str(exc))

        task_id = None if fm is None else fm.get("refresh_task")
        tasks, _settings, _errors = load_refresh_config(config.vault_root)
        pinned = (
            contract is not None
            and contract.policy == "auto"
            and task_id is not None
            and str(task_id) in tasks
            and tasks[str(task_id)].note == rel
        )
        if not pinned or contract is None:
            return ToolResult.failure(
                ErrorCode.VALIDATION_FAILED,
                f"refresh_apply refused for {rel}: not an executable auto contract",
            )

        # Server-managed contract fields: frontmatter round-trips through a
        # deep copy so OTHER fields (title, tags, ...) are preserved exactly.
        due = compute_due(today, contract.every)
        new_fm = copy.deepcopy(fm) if fm is not None else CommentedMap()
        new_fm["refresh_last"] = today.isoformat()
        new_fm["refresh_due"] = due.isoformat()
        new_fm["refresh_stale"] = False
        new_content = render_note(ParsedNote(frontmatter=new_fm, body=body))

        # Hooks run against the desired post-write state, BEFORE the
        # snapshot: a rejection here means zero side effects.
        if hooks is not None:
            try:
                run_validation_hooks(
                    hooks,
                    HookContext(
                        path=vp,
                        new_frontmatter=to_plain_dict(dict(new_fm)),
                        new_body=body,
                        operation=tool_name,
                    ),
                )
            except Exception as exc:
                return map_exception(exc)

        # Snapshot BEFORE any write (idiom: destructive.py / snapshot_for_destruction).
        try:
            snapshot_id = snapshot_for_destruction(
                vp, snapshot_root=config.vault_root / _TRASH_DIRNAME
            )
        except SnapshotError as exc:
            emit_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                op_kind="write",
                vault_path=rel,
                outcome="failure",
                started=started,
                params_hash=payload_hash_value,
                dry_run=False,
            )
            return ToolResult.failure(
                ErrorCode.INTERNAL_ERROR,
                f"snapshot failed before refresh_apply: {exc}",
            )

        atomic_write_text(vp, new_content)

        emit_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            op_kind="write",
            vault_path=rel,
            outcome="success",
            started=started,
            params_hash=payload_hash_value,
            dry_run=False,
            snapshot_id=snapshot_id,
        )
        return ToolResult.success(
            data={
                "path": rel,
                "snapshot_id": snapshot_id,
                "refresh_last": today.isoformat(),
                "refresh_due": due.isoformat(),
            }
        )
    except Exception as exc:
        # Reached only for failures AFTER the snapshot already succeeded
        # (e.g. `atomic_write_text` raising): the snapshot audit above
        # covers a snapshot that never happened. Precondition refusals and
        # hook rejections return directly above and never reach here.
        if snapshot_id is not None and vp is not None:
            emit_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                op_kind="write",
                vault_path=str(vp.relative),
                outcome="failure",
                started=started,
                params_hash=payload_hash_value,
                dry_run=False,
                snapshot_id=snapshot_id,
            )
        return map_exception(exc)
