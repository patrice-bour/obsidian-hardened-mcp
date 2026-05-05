# SPDX-License-Identifier: Apache-2.0
"""Pluggable validation hooks.

Hooks are short, focused validators that run BEFORE any write or destructive
operation. The runtime constructs a `HookContext` describing the desired
post-write state, then asks each hook in order whether it accepts the
operation. The first `reject` short-circuits and aborts. `warn` results
accumulate into the report and are surfaced to the caller without blocking.

Hooks are loaded from `.obsidian-hardened-mcp.yaml` at the vault root (see
`validation.config_loader`). Built-in hooks live in `validation.builtin_hooks`;
plugins can register their own classes implementing the `ValidationHook`
Protocol.

Threat-model role: hooks are the *configurable* layer of safety, on top of
the non-negotiable baseline (path sandbox, atomic writer, YAML safety).
A buggy or malicious hook can REFUSE writes but cannot bypass the baseline.
A crashing hook is treated as a rejection — the registry never opens the
door because of an unexpected exception.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from obsidian_hardened_mcp.domain.vault_path import VaultPath

Phase = Literal["pre_write"]
Decision = Literal["accept", "warn", "reject"]


@dataclass(frozen=True)
class HookContext:
    """Snapshot of the operation a hook is asked to validate.

    The hook sees the DESIRED post-write state (`new_frontmatter`, `new_body`)
    so it can validate the result without re-deriving it. `operation` is the
    name of the calling tool (e.g. `"set_frontmatter_field"`).
    """

    path: VaultPath
    new_frontmatter: dict[str, Any] | None
    new_body: str
    operation: str


@dataclass(frozen=True)
class HookResult:
    """Outcome of a single hook.

    Use the factory classmethods rather than constructing directly — the
    decision/reason invariant is easier to read at call sites.
    """

    decision: Decision
    reason: str | None = None

    @classmethod
    def accept(cls) -> HookResult:
        return cls(decision="accept", reason=None)

    @classmethod
    def warn(cls, reason: str) -> HookResult:
        return cls(decision="warn", reason=reason)

    @classmethod
    def reject(cls, reason: str) -> HookResult:
        return cls(decision="reject", reason=reason)

    @property
    def is_blocking(self) -> bool:
        return self.decision == "reject"


@runtime_checkable
class ValidationHook(Protocol):
    """Structural protocol every hook implements.

    `name` should be a stable, snake_case identifier used in error messages
    and configuration. `phase` is `"pre_write"` for v0.1 — `"post_read"` and
    `"post_write"` are reserved for future expansion.
    """

    name: str
    phase: Phase

    def validate(self, ctx: HookContext) -> HookResult: ...


# ---------------------------------------------------------------------------
# Reports + violation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Warning:
    hook_name: str
    reason: str


@dataclass(frozen=True)
class _Rejection:
    hook_name: str
    reason: str


@dataclass(frozen=True)
class HookReport:
    """Aggregated outcome of running a registry of hooks against a context."""

    allowed: bool
    warnings: list[_Warning] = field(default_factory=list)
    rejection: _Rejection | None = None

    def raise_for_rejection(self) -> None:
        """Raise `HookViolationError` if any hook rejected the operation."""
        if self.rejection is not None:
            raise HookViolationError(
                hook_name=self.rejection.hook_name,
                reason=self.rejection.reason,
            )


class HookViolationError(Exception):
    """A pre-write hook rejected the operation."""

    def __init__(self, hook_name: str, reason: str) -> None:
        super().__init__(f"hook {hook_name!r} rejected the operation: {reason}")
        self.hook_name = hook_name
        self.reason = reason


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class HookRegistry:
    """Ordered collection of hooks; runs them sequentially."""

    def __init__(self, hooks: list[ValidationHook]) -> None:
        self._hooks = list(hooks)

    @property
    def hooks(self) -> tuple[ValidationHook, ...]:
        return tuple(self._hooks)

    def run(self, ctx: HookContext) -> HookReport:
        """Run all hooks against `ctx`. Stops at the first reject (or hook
        exception). Warnings accumulate; accepts are the default.

        Mutation isolation: each hook receives a fresh deep-copy of
        `ctx.new_frontmatter` and `ctx.new_body` so that a mutating hook
        cannot leak state to the next hook in the registry, nor to the
        caller. The caller's view of the original `ctx` is also preserved.
        """
        warnings: list[_Warning] = []
        for hook in self._hooks:
            isolated_ctx = HookContext(
                path=ctx.path,
                new_frontmatter=copy.deepcopy(ctx.new_frontmatter),
                new_body=ctx.new_body,
                operation=ctx.operation,
            )
            try:
                result = hook.validate(isolated_ctx)
            except Exception as exc:
                # A crashing hook MUST NOT be treated as "accept". Convert it
                # into a rejection so the calling tool aborts the write.
                return HookReport(
                    allowed=False,
                    warnings=warnings,
                    rejection=_Rejection(
                        hook_name=getattr(hook, "name", type(hook).__name__),
                        reason=f"hook raised {type(exc).__name__}: {exc}",
                    ),
                )
            if result.decision == "reject":
                assert result.reason is not None
                return HookReport(
                    allowed=False,
                    warnings=warnings,
                    rejection=_Rejection(
                        hook_name=hook.name, reason=result.reason
                    ),
                )
            if result.decision == "warn":
                assert result.reason is not None
                warnings.append(_Warning(hook_name=hook.name, reason=result.reason))
        return HookReport(allowed=True, warnings=warnings, rejection=None)
