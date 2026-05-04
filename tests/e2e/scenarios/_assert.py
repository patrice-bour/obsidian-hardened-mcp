"""Tiny assertion helpers shared across scenarios.

A scenario reports its outcome by appending `Step` entries to a list and
returning it. The runner formats the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_harness import CallResult


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ScenarioReport:
    code: str         # e.g. "S0"
    title: str        # e.g. "smoke"
    steps: list[Step] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.steps if s.ok)

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def all_ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append(Step(name=name, ok=ok, detail=detail))


def expect_ok(result: CallResult, *, where: str = "") -> tuple[bool, str]:
    if result.ok:
        return True, ""
    code = result.error_code or "?"
    msg = result.error_message or ""
    return False, f"{where}: expected ok, got error_code={code} {msg!r}"[:200]


def expect_error(
    result: CallResult, code: str, *, where: str = ""
) -> tuple[bool, str]:
    if result.ok:
        return False, f"{where}: expected error {code}, got ok=True"
    actual = result.error_code or "?"
    if actual != code:
        msg = (
            f"{where}: expected error {code}, got {actual} "
            f"({result.error_message!r})"
        )
        return False, msg[:200]
    return True, ""


def expect_data_contains(
    result: CallResult, key: str, *, where: str = ""
) -> tuple[bool, str]:
    if not result.ok:
        return False, f"{where}: ok=False, can't probe data"
    if not result.data or key not in result.data:
        keys = list((result.data or {}).keys())
        return False, f"{where}: missing key {key!r} (have {keys})"
    return True, ""


def field_value(result: CallResult, key: str) -> Any:
    return (result.data or {}).get(key)
