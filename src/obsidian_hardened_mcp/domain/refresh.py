# SPDX-License-Identifier: Apache-2.0
"""Refresh contract — vault-refresh v1.

A note opts in by carrying `refresh_every` AND `refresh_last` in its
frontmatter. Pure domain module: no I/O, stdlib only. Calendar arithmetic
clamps to the end of month (Jan 31 + 1m -> Feb 28/29), no dateutil.
"""

from __future__ import annotations

import datetime as dt
import re
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass

POLICIES: tuple[str, ...] = ("auto", "on_read", "flag")
DEFAULT_POLICY = "flag"

_INTERVAL_RE = re.compile(r"^([1-9]\d*)([dwmy])$")


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
    return int(m.group(1)), m.group(2)


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


def parse_contract(fm: Mapping | None) -> RefreshContract | None:
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
