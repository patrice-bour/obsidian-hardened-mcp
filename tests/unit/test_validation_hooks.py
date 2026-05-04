"""Tests for the validation Hook Protocol + HookRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_full_mcp.domain.vault_path import VaultPath
from obsidian_full_mcp.validation.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    HookViolationError,
    ValidationHook,
)


def _ctx(tmp_vault: Path, **overrides: object) -> HookContext:
    base: dict[str, object] = {
        "path": VaultPath.from_user("01_Notes/sample.md", tmp_vault),
        "new_frontmatter": {"title": "Hello"},
        "new_body": "Body\n",
        "operation": "create_note",
    }
    base.update(overrides)
    return HookContext(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HookResult
# ---------------------------------------------------------------------------


class TestHookResult:
    def test_accept_factory(self) -> None:
        r = HookResult.accept()
        assert r.decision == "accept"
        assert r.reason is None
        assert r.is_blocking is False

    def test_reject_requires_reason(self) -> None:
        r = HookResult.reject("invalid date")
        assert r.decision == "reject"
        assert r.reason == "invalid date"
        assert r.is_blocking is True

    def test_warn_carries_reason_but_does_not_block(self) -> None:
        r = HookResult.warn("dubious tag")
        assert r.decision == "warn"
        assert r.reason == "dubious tag"
        assert r.is_blocking is False


# ---------------------------------------------------------------------------
# HookRegistry — execution order and decision aggregation
# ---------------------------------------------------------------------------


class _AlwaysAccept:
    name = "always_accept"
    phase = "pre_write"

    def validate(self, ctx: HookContext) -> HookResult:
        return HookResult.accept()


class _AlwaysReject:
    name = "always_reject"
    phase = "pre_write"

    def __init__(self, reason: str = "no") -> None:
        self.reason = reason

    def validate(self, ctx: HookContext) -> HookResult:
        return HookResult.reject(self.reason)


class _AlwaysWarn:
    name = "always_warn"
    phase = "pre_write"

    def validate(self, ctx: HookContext) -> HookResult:
        return HookResult.warn("careful")


class _RecordsCalls:
    name = "records"
    phase = "pre_write"

    def __init__(self) -> None:
        self.calls: list[HookContext] = []

    def validate(self, ctx: HookContext) -> HookResult:
        self.calls.append(ctx)
        return HookResult.accept()


class TestHookRegistry:
    def test_empty_registry_accepts(self, tmp_vault: Path) -> None:
        reg = HookRegistry([])
        ctx = _ctx(tmp_vault)
        report = reg.run(ctx)
        assert report.allowed is True
        assert report.warnings == []
        assert report.rejection is None

    def test_single_accept_hook(self, tmp_vault: Path) -> None:
        reg = HookRegistry([_AlwaysAccept()])
        report = reg.run(_ctx(tmp_vault))
        assert report.allowed is True

    def test_first_reject_short_circuits_remaining_hooks(
        self, tmp_vault: Path
    ) -> None:
        recorder = _RecordsCalls()
        reg = HookRegistry([_AlwaysReject("nope"), recorder])
        report = reg.run(_ctx(tmp_vault))
        assert report.allowed is False
        assert report.rejection is not None
        assert report.rejection.hook_name == "always_reject"
        assert report.rejection.reason == "nope"
        # The recorder must NOT have been called.
        assert recorder.calls == []

    def test_warnings_accumulate_but_do_not_block(self, tmp_vault: Path) -> None:
        reg = HookRegistry([_AlwaysWarn(), _AlwaysAccept(), _AlwaysWarn()])
        report = reg.run(_ctx(tmp_vault))
        assert report.allowed is True
        assert len(report.warnings) == 2
        assert all(w.hook_name == "always_warn" for w in report.warnings)

    def test_hooks_run_in_declared_order(self, tmp_vault: Path) -> None:
        order: list[str] = []

        class Recording:
            phase = "pre_write"

            def __init__(self, name: str) -> None:
                self.name = name

            def validate(self, ctx: HookContext) -> HookResult:
                order.append(self.name)
                return HookResult.accept()

        reg = HookRegistry([Recording("first"), Recording("second"), Recording("third")])
        reg.run(_ctx(tmp_vault))
        assert order == ["first", "second", "third"]

    def test_unexpected_hook_exception_becomes_rejection(
        self, tmp_vault: Path
    ) -> None:
        class Boom:
            name = "boom"
            phase = "pre_write"

            def validate(self, ctx: HookContext) -> HookResult:
                raise RuntimeError("hook crashed")

        reg = HookRegistry([Boom()])
        report = reg.run(_ctx(tmp_vault))
        # A buggy hook MUST NOT silently allow a write through.
        assert report.allowed is False
        assert report.rejection is not None
        assert report.rejection.hook_name == "boom"
        assert "hook crashed" in report.rejection.reason


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestValidationHookProtocol:
    def test_accept_hook_is_a_validation_hook(self) -> None:
        # Structural-typing check via isinstance(... , ValidationHook).
        assert isinstance(_AlwaysAccept(), ValidationHook)

    def test_object_missing_validate_is_not_a_hook(self) -> None:
        class NotAHook:
            name = "x"
            phase = "pre_write"

        assert not isinstance(NotAHook(), ValidationHook)


# ---------------------------------------------------------------------------
# raise_for_rejection helper
# ---------------------------------------------------------------------------


def test_raise_for_rejection_raises_when_blocked(tmp_vault: Path) -> None:
    reg = HookRegistry([_AlwaysReject("schema mismatch")])
    report = reg.run(_ctx(tmp_vault))
    with pytest.raises(HookViolationError) as exc_info:
        report.raise_for_rejection()
    assert "always_reject" in str(exc_info.value)
    assert "schema mismatch" in str(exc_info.value)


def test_raise_for_rejection_is_noop_when_allowed(tmp_vault: Path) -> None:
    reg = HookRegistry([_AlwaysAccept()])
    report = reg.run(_ctx(tmp_vault))
    # No exception expected.
    report.raise_for_rejection()
