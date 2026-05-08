"""Wrapper-level tests for ctx.elicit out-of-band confirmation (M6-11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode


@pytest.fixture
def config(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault)


@pytest.fixture
def config_optout(tmp_vault: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, require_elicitation=False)


def _mock_ctx(elicit_action: str = "accept", confirm: bool = True) -> Any:
    """Build a mock Context whose `elicit` returns the configured result."""
    ctx = MagicMock()
    result = MagicMock()
    result.action = elicit_action
    result.data = MagicMock()
    result.data.confirm = confirm
    ctx.elicit = AsyncMock(return_value=result)
    return ctx


def _mock_ctx_unsupported() -> Any:
    """Build a mock Context whose `elicit` raises (simulating no client support)."""
    ctx = MagicMock()
    ctx.elicit = AsyncMock(side_effect=Exception("client does not support elicit"))
    return ctx


class TestRunElicitGate:
    """Tests for the small helper that wraps ctx.elicit with policy."""

    @pytest.mark.asyncio
    async def test_accept_returns_true(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.server import _run_elicit_gate

        ctx = _mock_ctx(elicit_action="accept", confirm=True)
        outcome = await _run_elicit_gate(
            ctx, message="Confirm delete?", config=config
        )
        assert outcome.accepted is True
        assert outcome.error_code is None

    @pytest.mark.asyncio
    async def test_reject_returns_rejected_code(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.server import _run_elicit_gate

        ctx = _mock_ctx(elicit_action="reject", confirm=False)
        outcome = await _run_elicit_gate(
            ctx, message="Confirm delete?", config=config
        )
        assert outcome.accepted is False
        assert outcome.error_code is ErrorCode.ELICITATION_REJECTED

    @pytest.mark.asyncio
    async def test_accept_but_confirm_false(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.server import _run_elicit_gate

        ctx = _mock_ctx(elicit_action="accept", confirm=False)
        outcome = await _run_elicit_gate(
            ctx, message="Confirm delete?", config=config
        )
        assert outcome.accepted is False
        assert outcome.error_code is ErrorCode.ELICITATION_REJECTED

    @pytest.mark.asyncio
    async def test_unsupported_strict(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.server import _run_elicit_gate

        ctx = _mock_ctx_unsupported()
        outcome = await _run_elicit_gate(
            ctx, message="Confirm delete?", config=config
        )
        assert outcome.accepted is False
        assert outcome.error_code is ErrorCode.ELICITATION_UNSUPPORTED

    @pytest.mark.asyncio
    async def test_unsupported_optout(self, config_optout: AppConfig) -> None:
        from obsidian_hardened_mcp.server import _run_elicit_gate

        ctx = _mock_ctx_unsupported()
        outcome = await _run_elicit_gate(
            ctx, message="Confirm delete?", config=config_optout
        )
        assert outcome.accepted is True
        assert outcome.error_code is None

    @pytest.mark.asyncio
    async def test_message_passed_through(self, config: AppConfig) -> None:
        from obsidian_hardened_mcp.server import _run_elicit_gate

        ctx = _mock_ctx(elicit_action="accept", confirm=True)
        await _run_elicit_gate(
            ctx, message="Confirm delete on notes/x.md?", config=config
        )
        ctx.elicit.assert_called_once()
        kwargs = ctx.elicit.call_args.kwargs
        assert "notes/x.md" in kwargs["message"]
