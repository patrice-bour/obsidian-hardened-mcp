# SPDX-License-Identifier: Apache-2.0
"""Vault-only refresh execution core.

`run_cycle` is the executor's single entry point: it scans the vault for
stale, executable `auto`-policy notes (the server's `list_stale_notes`),
calls an injected LLM per task, and applies guarded results through the
server's `refresh_apply` — the SAME audits/hooks path the MCP server
itself uses for writes. This module never mutates a note by any other
means.

Per-task errors (a bad LLM call, a rejected write, ...) are isolated: one
failing task becomes an "anomaly" `TaskResult` and every other task in the
cycle still runs to completion.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.refresh import RefreshTask
from obsidian_hardened_mcp.frontmatter import parse_note
from obsidian_hardened_mcp.security.audit_logger import AuditLogger
from obsidian_hardened_mcp.tools.read import read_note
from obsidian_hardened_mcp.tools.refresh import list_stale_notes, refresh_apply
from obsidian_hardened_mcp.validation.config_loader import (
    load_refresh_config,
    load_validation_config,
)
from obsidian_hardened_mcp.validation.hooks import HookRegistry

LlmComplete = Callable[[str, list[dict[str, str]]], tuple[str, float]]
"""`(route, messages) -> (new markdown body, cost in USD)`."""

WebSearch = Callable[[str], str]
"""`query -> text block of search results`."""

_SYSTEM_MESSAGE = (
    "You update ONE Obsidian note. Return ONLY the full new markdown body, "
    "no frontmatter."
)
_FALLBACK_ROUTE = "local-thinker"


@dataclass(frozen=True)
class TaskResult:
    """Outcome of executing one refresh task against one note."""

    task_id: str
    path: str
    status: str  # "applied" | "skipped" | "anomaly"
    reason: str
    model: str
    cost: float


@dataclass(frozen=True)
class CycleReport:
    """Aggregate outcome of one `run_cycle` call."""

    results: list[TaskResult]
    total_cost: float


def run_cycle(
    vault_root: Path,
    *,
    llm_complete: LlmComplete,
    web_search: WebSearch | None = None,
    today: date | None = None,
    dry_run: bool = False,
    only_task: str | None = None,
) -> CycleReport:
    """Scan `vault_root` for stale, executable `auto` tasks and run each one.

    `only_task`, when set, restricts the cycle to the single `refresh_task`
    id given: every other executable entry is skipped BEFORE any LLM/web
    call — the cheapest possible filter, applied against the scan result
    the same way the `executable` check already is, so it costs nothing and
    never touches the guards or `_run_task` for tasks that don't match.

    Builds `AppConfig` via `AppConfig.from_env` (the same call the MCP
    server's own entry point makes), so it honours `OBSIDIAN_AUDIT_DIR`
    while defaulting to the server's audit directory otherwise. Also loads
    the vault's `.obsidian-hardened-mcp.yaml` validation hooks the same
    way `create_server` does, so `refresh_apply` runs under the SAME
    pre-write validation the MCP server enforces for every other write
    path — the executor has no separate, weaker write door. A scan
    failure (e.g. an unreadable vault root) yields an empty report rather
    than raising — the executor is meant to run unattended.

    A task that declares the `"web"` tool has its `web_queries` searched
    via `web_search` (one call per declared query, results concatenated
    into the user message) — see `_build_messages`. A task declaring
    `"web"` while `web_search` is `None` (no API key configured) is an
    anomaly (`"web unavailable"`), never a silent no-op.

    Scan/config anomalies (a typo'd whitelist entry, an unreadable note, a
    task/note pinning mismatch, ...) are folded into the returned
    `CycleReport` as `TaskResult`s with `status="anomaly"` — so they show
    up in `_print_report`'s CLI output and in the report note, exactly like
    a failed task. `load_refresh_config`'s own error messages are NOT
    re-added here: `list_stale_notes` already calls `load_refresh_config`
    itself and folds its `InvalidTaskError` messages into
    `scan.data["anomalies"]` (under `path=CONFIG_FILE_NAME`), so doing it
    again here would double-report the same broken whitelist entry.
    """
    config = AppConfig.from_env(vault_root)
    audit = AuditLogger(audit_dir=config.audit_dir)
    hooks: HookRegistry = load_validation_config(config.vault_root)

    tasks, settings, _config_errors = load_refresh_config(config.vault_root)

    scan = list_stale_notes(config, audit, today=today, hooks=hooks)
    if not scan.ok or scan.data is None:
        return CycleReport(results=[], total_cost=0.0)

    results: list[TaskResult] = []
    total_cost = 0.0

    for anomaly in scan.data.get("anomalies", []):
        results.append(
            TaskResult(
                task_id="<config>",
                path=str(anomaly.get("path", "")),
                status="anomaly",
                reason=str(anomaly.get("reason", "")),
                model="",
                cost=0.0,
            )
        )

    for entry in scan.data.get("stale", []):
        if not entry.get("executable"):
            continue
        if only_task is not None and str(entry["task"]) != only_task:
            continue
        result = _run_task(
            config,
            audit,
            task_id=str(entry["task"]),
            path=str(entry["path"]),
            tasks=tasks,
            min_body_ratio=settings.min_body_ratio,
            local_routes=settings.local_routes,
            max_usd_per_cycle=settings.max_usd_per_cycle,
            total_cost_so_far=total_cost,
            llm_complete=llm_complete,
            web_search=web_search,
            today=today,
            dry_run=dry_run,
            hooks=hooks,
        )
        results.append(result)
        total_cost += result.cost

    return CycleReport(results=results, total_cost=total_cost)


def _run_task(
    config: AppConfig,
    audit: AuditLogger,
    *,
    task_id: str,
    path: str,
    tasks: dict[str, RefreshTask],
    min_body_ratio: float,
    local_routes: tuple[str, ...],
    max_usd_per_cycle: float,
    total_cost_so_far: float,
    llm_complete: LlmComplete,
    web_search: WebSearch | None,
    today: date | None,
    dry_run: bool,
    hooks: HookRegistry,
) -> TaskResult:
    """Execute one task end to end, isolating any failure into an anomaly.

    Route selection: `task.model or (local_routes[0] if local_routes else
    "local-thinker")`. Three guards run before any LLM call:

    - **Route guard**: a route outside `local_routes` (falling back to
      just `"local-thinker"` when `local_routes` is unset) is refused
      unless the task carries the `"cloud"` tool — anomaly
      `cloud route not allowed`.
    - **Cost cap**: once the cycle's running cost so far exceeds
      `max_usd_per_cycle`, further NON-LOCAL-routed tasks are stopped —
      anomaly `cost cap reached`. The cap keys on the RESOLVED route,
      not the declared `"cloud"` tool: only a call that can actually
      bill is worth stopping. Locally-routed tasks (including tasks
      that declare `"cloud"` but resolve to a local route) are never
      subject to the cap and keep running.
    - **Web guard**: a task carrying the `"web"` tool with `web_search`
      unavailable (no API key configured) is refused — anomaly
      `web unavailable` — rather than silently skipping the search.

    Any exception raised while reading the note, searching the web, calling
    the LLM, or applying the result is caught here so one bad task never
    aborts the rest of the cycle.
    """
    task = tasks.get(task_id)
    if task is None:
        return TaskResult(
            task_id=task_id,
            path=path,
            status="anomaly",
            reason=f"unknown refresh_task: {task_id!r}",
            model="",
            cost=0.0,
        )

    allowed_routes = local_routes if local_routes else (_FALLBACK_ROUTE,)
    route = task.model or allowed_routes[0]
    is_local_route = route in allowed_routes

    if not is_local_route and "cloud" not in task.tools:
        return TaskResult(
            task_id=task_id,
            path=path,
            status="anomaly",
            reason="cloud route not allowed",
            model=route,
            cost=0.0,
        )

    if not is_local_route and total_cost_so_far > max_usd_per_cycle:
        return TaskResult(
            task_id=task_id,
            path=path,
            status="anomaly",
            reason="cost cap reached",
            model=route,
            cost=0.0,
        )

    if "web" in task.tools and web_search is None:
        return TaskResult(
            task_id=task_id,
            path=path,
            status="anomaly",
            reason="web unavailable",
            model=route,
            cost=0.0,
        )

    try:
        current_body = _read_current_body(config, path)
        messages = _build_messages(task, current_body, web_search=web_search)
        new_body, cost = llm_complete(route, messages)
    except Exception as exc:  # per-task isolation is the point: never abort the cycle
        return TaskResult(
            task_id=task_id,
            path=path,
            status="anomaly",
            reason=f"{type(exc).__name__}: {exc}",
            model=route,
            cost=0.0,
        )

    guard_reason = _output_guard_reason(new_body, current_body, min_body_ratio)
    if guard_reason is not None:
        return TaskResult(
            task_id=task_id, path=path, status="anomaly", reason=guard_reason,
            model=route, cost=cost,
        )

    if dry_run:
        return TaskResult(
            task_id=task_id, path=path, status="skipped", reason="dry-run",
            model=route, cost=cost,
        )

    apply_result = refresh_apply(config, audit, path, new_body, hooks=hooks, today=today)
    if not apply_result.ok:
        reason = apply_result.error.message if apply_result.error else "refresh_apply failed"
        return TaskResult(
            task_id=task_id, path=path, status="anomaly", reason=reason,
            model=route, cost=cost,
        )

    return TaskResult(
        task_id=task_id, path=path, status="applied", reason="", model=route, cost=cost,
    )


def _read_current_body(config: AppConfig, path: str) -> str:
    """Read the note's current body (frontmatter stripped) via `read_note`."""
    read_result = read_note(config, path)
    if not read_result.ok or read_result.data is None:
        reason = read_result.error.message if read_result.error else "read_note failed"
        raise RuntimeError(reason)
    content = str(read_result.data["content"])
    return parse_note(content).body


def _build_messages(
    task: RefreshTask, current_body: str, *, web_search: WebSearch | None
) -> list[dict[str, str]]:
    """System message (fixed contract) + user message (whitelisted prompt,
    current body, optional web-search blocks).

    SECURITY INVARIANT: only the task's DECLARED `web_queries` are ever
    searched — one `web_search` call per declared query, nothing derived
    from note content or LLM output. By the time this runs, `_run_task`'s
    web guard has already ensured `web_search` is not `None` whenever
    `"web" in task.tools`; the `is not None` check here is defensive.
    """
    web_blocks = ""
    if "web" in task.tools and web_search is not None:
        blocks = [web_search(query) for query in task.web_queries]
        if blocks:
            web_blocks = "\n\n---\n\n" + "\n\n".join(blocks)
    user_content = f"{task.prompt}\n\n---\n\n{current_body}{web_blocks}"
    return [
        {"role": "system", "content": _SYSTEM_MESSAGE},
        {"role": "user", "content": user_content},
    ]


def _output_guard_reason(
    new_body: str, previous_body: str, min_body_ratio: float
) -> str | None:
    """Refuse an LLM reply that looks broken, before it ever reaches
    `refresh_apply`: empty, suspiciously short vs. the previous body, or
    accidentally carrying a frontmatter delimiter into the body slot."""
    if not new_body.strip():
        return "empty body"
    if new_body.startswith("---"):
        return "body starts with frontmatter delimiter (---)"
    if previous_body and len(new_body) < min_body_ratio * len(previous_body):
        return (
            f"body too short: {len(new_body)} chars < "
            f"{min_body_ratio:.0%} of previous {len(previous_body)} chars"
        )
    return None
