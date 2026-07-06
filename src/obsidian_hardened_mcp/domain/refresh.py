# SPDX-License-Identifier: Apache-2.0
"""Refresh contract — vault-refresh v1.

A note opts in by carrying `refresh_every` AND `refresh_last` in its
frontmatter. Pure domain module: no I/O, stdlib only. Calendar arithmetic
clamps to the end of month (Jan 31 + 1m -> Feb 28/29), no dateutil.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

POLICIES: tuple[str, ...] = ("auto", "on_read", "flag")
DEFAULT_POLICY = "flag"

_INTERVAL_RE = re.compile(r"^([1-9]\d*)([dwmy])$")

# Sane ceilings per unit (~100 years), so a typo'd `refresh_every` cannot
# push `compute_due` into `dt.date`/`OverflowError` territory.
_MAX_BY_UNIT: dict[str, int] = {"d": 36500, "w": 5200, "m": 1200, "y": 100}


class InvalidContractError(ValueError):
    """A note declares refresh_* fields but the contract is unusable."""


@dataclass(frozen=True)
class RefreshContract:
    policy: str
    every: str
    last: dt.date
    prompt: str | None


def parse_interval(raw: str) -> tuple[int, str]:
    """`"7d"` -> `(7, "d")`. Units: d(ays), w(eeks), m(onths), y(ears)."""
    m = _INTERVAL_RE.match(str(raw))
    if m is None:
        raise InvalidContractError(
            f"invalid refresh_every: {raw!r} (expected <int><d|w|m|y>, e.g. '1m')"
        )
    n, unit = int(m.group(1)), m.group(2)
    max_n = _MAX_BY_UNIT[unit]
    if n > max_n:
        raise InvalidContractError(
            f"invalid refresh_every: {raw!r} (magnitude exceeds max {max_n}{unit})"
        )
    return n, unit


def _add_months(day: dt.date, months: int) -> dt.date:
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return dt.date(year, month, min(day.day, monthrange(year, month)[1]))


def compute_due(last: dt.date, every: str) -> dt.date:
    n, unit = parse_interval(every)
    if unit == "d":
        return last + dt.timedelta(days=n)
    if unit == "w":
        return last + dt.timedelta(weeks=n)
    if unit == "m":
        return _add_months(last, n)
    return _add_months(last, 12 * n)


def _coerce_date(value: object, *, field: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidContractError(f"invalid {field}: {value!r}") from exc
    raise InvalidContractError(f"invalid {field}: {value!r}")


def parse_contract(fm: Mapping[str, Any] | None) -> RefreshContract | None:
    """Extract the refresh contract from a frontmatter mapping.

    Returns None when the note is not under contract (neither
    `refresh_every` nor `refresh_last` present). Raises
    `InvalidContractError` on a partial or malformed contract.
    """
    if not fm:
        return None
    every = fm.get("refresh_every")
    last = fm.get("refresh_last")
    if every is None and last is None:
        return None
    if every is None:
        raise InvalidContractError("refresh_every is required when refresh_last is set")
    if last is None:
        raise InvalidContractError("refresh_last is required when refresh_every is set")
    parse_interval(str(every))  # validation only
    policy = fm.get("refresh_policy", DEFAULT_POLICY)
    if policy not in POLICIES:
        raise InvalidContractError(
            f"invalid refresh_policy: {policy!r} (expected one of {POLICIES})"
        )
    prompt = fm.get("refresh_prompt")
    return RefreshContract(
        policy=str(policy),
        every=str(every),
        last=_coerce_date(last, field="refresh_last"),
        prompt=None if prompt is None else str(prompt),
    )


ALLOWED_TASK_TOOLS: frozenset[str] = frozenset({"vault", "web", "cloud"})


class InvalidTaskError(ValueError):
    """A `refresh_tasks:` whitelist entry is unusable."""


@dataclass(frozen=True)
class RefreshTask:
    task_id: str
    note: str
    prompt: str
    tools: frozenset[str]
    model: str | None
    web_queries: tuple[str, ...]


@dataclass(frozen=True)
class ExecutorSettings:
    max_usd_per_cycle: float = 0.50
    min_body_ratio: float = 0.3
    local_routes: tuple[str, ...] = ()


def parse_refresh_task(task_id: str, raw: Mapping[str, Any]) -> RefreshTask:
    note = str(raw.get("note") or "").strip()
    if not note:
        raise InvalidTaskError(f"task {task_id!r}: note is required")
    # Pinning comparisons (scan + `refresh_apply`) both key off `VaultPath`'s
    # NFC-normalised relative path (`domain.vault_path.VaultPath.from_user`).
    # Normalize the whitelist side the same way here, once, at parse time —
    # so a `./`-prefixed or NFD-typed `note:` still pins correctly instead of
    # silently failing closed on a macOS/iCloud filename encoding mismatch.
    note = unicodedata.normalize("NFC", note)
    if note.startswith("./"):
        note = note[2:]
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise InvalidTaskError(f"task {task_id!r}: prompt is required")
    tools_raw = raw.get("tools")
    if tools_raw is None:
        tools_raw = ["vault"]
    elif not isinstance(tools_raw, list):
        raise InvalidTaskError(
            f"task {task_id!r}: tools must be a list, got {type(tools_raw).__name__}"
        )
    tools = frozenset(str(t) for t in tools_raw) | {"vault"}
    if not tools <= ALLOWED_TASK_TOOLS:
        raise InvalidTaskError(
            f"task {task_id!r}: tools must be a subset of {sorted(ALLOWED_TASK_TOOLS)}"
        )
    queries_raw = raw.get("web_queries")
    if queries_raw is None:
        queries_raw = []
    elif not isinstance(queries_raw, list):
        raise InvalidTaskError(
            f"task {task_id!r}: web_queries must be a list, "
            f"got {type(queries_raw).__name__}"
        )
    queries = tuple(str(q) for q in queries_raw)
    if "web" in tools and not queries:
        raise InvalidTaskError(f"task {task_id!r}: web requires web_queries")
    model = raw.get("model")
    return RefreshTask(
        task_id=task_id,
        note=note,
        prompt=prompt,
        tools=tools,
        model=None if model is None else str(model),
        web_queries=queries,
    )
