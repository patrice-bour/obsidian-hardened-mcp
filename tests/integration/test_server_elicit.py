"""Wrapper-level tests for ctx.elicit out-of-band confirmation (M6-11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from obsidian_hardened_mcp.config import AppConfig
from obsidian_hardened_mcp.domain.results import ErrorCode, ToolResult


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


# ---------------------------------------------------------------------------
# Harness fixtures for wrapper-level tests
# ---------------------------------------------------------------------------


@pytest.fixture
def harness(tmp_vault: Path) -> Any:
    """Build a thin adapter around the @app.tool registrations for testing.

    Strategy: instantiate `create_server` with the test config, then
    introspect FastMCP's tool registry to retrieve the registered async
    tool functions. Call them directly with the mock ctx.
    """
    from obsidian_hardened_mcp.server import create_server

    cfg = AppConfig(vault_root=tmp_vault)
    server = create_server(cfg)

    class _Harness:
        async def delete_note(self, ctx: Any, **kwargs: Any) -> ToolResult:
            tool_fn = server._tool_manager._tools["delete_note"].fn
            return await tool_fn(ctx=ctx, **kwargs)

        async def execute_command(self, ctx: Any, **kwargs: Any) -> ToolResult:
            tool_fn = server._tool_manager._tools["execute_command"].fn
            return await tool_fn(ctx=ctx, **kwargs)

    return _Harness()


@pytest.fixture
def harness_optout(tmp_vault: Path) -> Any:
    """Same as `harness` but with require_elicitation=False."""
    from obsidian_hardened_mcp.server import create_server

    cfg = AppConfig(vault_root=tmp_vault, require_elicitation=False)
    server = create_server(cfg)

    class _Harness:
        async def delete_note(self, ctx: Any, **kwargs: Any) -> ToolResult:
            tool_fn = server._tool_manager._tools["delete_note"].fn
            return await tool_fn(ctx=ctx, **kwargs)

    return _Harness()


class TestDeleteNoteWrapper:
    """End-to-end behaviour of the @app.tool delete_note wrapper.

    These tests build a real FastMCP-style server with the existing
    impl and a mock ctx, and call the wrapper directly to verify
    elicit branching at Phase 2.
    """

    @pytest.mark.asyncio
    async def test_delete_phase1_no_elicit(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx = _mock_ctx(elicit_action="accept", confirm=True)
        result = await harness.delete_note(
            ctx, path="01_Notes/sample.md", confirm_token=None, dry_run=False
        )
        assert result.ok is True
        assert "confirm_token" in (result.data or {})
        ctx.elicit.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_dry_run_no_elicit(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx = _mock_ctx(elicit_action="accept", confirm=True)
        result = await harness.delete_note(
            ctx, path="01_Notes/sample.md", confirm_token=None, dry_run=True
        )
        assert result.ok is True
        ctx.elicit.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_phase2_dry_run_no_elicit(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness.delete_note(
            ctx, path="01_Notes/sample.md", confirm_token=None, dry_run=False
        )
        token = phase1.data["confirm_token"]
        ctx.elicit.reset_mock()
        result = await harness.delete_note(
            ctx, path="01_Notes/sample.md", confirm_token=token, dry_run=True
        )
        assert result.ok is True
        ctx.elicit.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_phase2_elicit_accept(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness.delete_note(
            ctx, path="01_Notes/sample.md", confirm_token=None, dry_run=False
        )
        token = phase1.data["confirm_token"]
        ctx.elicit.reset_mock()
        result = await harness.delete_note(
            ctx, path="01_Notes/sample.md", confirm_token=token, dry_run=False
        )
        assert result.ok is True
        ctx.elicit.assert_called_once()
        assert not (tmp_vault / "01_Notes" / "sample.md").exists()

    @pytest.mark.asyncio
    async def test_delete_phase2_elicit_reject(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx_accept = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness.delete_note(
            ctx_accept, path="01_Notes/sample.md", confirm_token=None, dry_run=False
        )
        token = phase1.data["confirm_token"]

        ctx_reject = _mock_ctx(elicit_action="reject", confirm=False)
        result = await harness.delete_note(
            ctx_reject, path="01_Notes/sample.md", confirm_token=token, dry_run=False
        )
        assert result.ok is False
        assert result.error.code is ErrorCode.ELICITATION_REJECTED
        assert (tmp_vault / "01_Notes" / "sample.md").exists()

    @pytest.mark.asyncio
    async def test_delete_phase2_elicit_unsupported_strict(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx_accept = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness.delete_note(
            ctx_accept, path="01_Notes/sample.md", confirm_token=None, dry_run=False
        )
        token = phase1.data["confirm_token"]

        ctx_unsup = _mock_ctx_unsupported()
        result = await harness.delete_note(
            ctx_unsup, path="01_Notes/sample.md", confirm_token=token, dry_run=False
        )
        assert result.ok is False
        assert result.error.code is ErrorCode.ELICITATION_UNSUPPORTED
        assert (tmp_vault / "01_Notes" / "sample.md").exists()

    @pytest.mark.asyncio
    async def test_delete_phase2_elicit_unsupported_optout(
        self, harness_optout: Any, tmp_vault: Path
    ) -> None:
        ctx_accept = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness_optout.delete_note(
            ctx_accept, path="01_Notes/sample.md", confirm_token=None, dry_run=False
        )
        token = phase1.data["confirm_token"]

        ctx_unsup = _mock_ctx_unsupported()
        result = await harness_optout.delete_note(
            ctx_unsup, path="01_Notes/sample.md", confirm_token=token, dry_run=False
        )
        assert result.ok is True
        assert not (tmp_vault / "01_Notes" / "sample.md").exists()

    @pytest.mark.asyncio
    async def test_elicit_message_contains_path(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx_accept = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness.delete_note(
            ctx_accept, path="01_Notes/sample.md", confirm_token=None, dry_run=False
        )
        token = phase1.data["confirm_token"]
        ctx_check = _mock_ctx(elicit_action="accept", confirm=True)
        await harness.delete_note(
            ctx_check, path="01_Notes/sample.md", confirm_token=token, dry_run=False
        )
        kwargs = ctx_check.elicit.call_args.kwargs
        assert "01_Notes/sample.md" in kwargs["message"]
        assert "delete" in kwargs["message"].lower()


class TestExecuteCommandWrapper:
    """Mirror of TestDeleteNoteWrapper for execute_command."""

    @pytest.mark.asyncio
    async def test_execute_command_phase1_no_elicit(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx = _mock_ctx(elicit_action="accept", confirm=True)
        await harness.execute_command(
            ctx,
            command_id="editor:save-file",
            confirm_token=None,
            dry_run=False,
        )
        # Phase 1 issues a token (or returns REST_UNAVAILABLE if REST is
        # missing). We assert ONLY: elicit not called.
        ctx.elicit.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_command_phase2_elicit_accept(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx_accept = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness.execute_command(
            ctx_accept,
            command_id="editor:save-file",
            confirm_token=None,
            dry_run=False,
        )
        token = (phase1.data or {}).get("confirm_token")
        if token is None:
            pytest.skip("Phase 1 did not issue a token (REST unavailable)")

        ctx_check = _mock_ctx(elicit_action="accept", confirm=True)
        await harness.execute_command(
            ctx_check,
            command_id="editor:save-file",
            confirm_token=token,
            dry_run=False,
        )
        ctx_check.elicit.assert_called_once()
        kwargs = ctx_check.elicit.call_args.kwargs
        assert "editor:save-file" in kwargs["message"]

    @pytest.mark.asyncio
    async def test_execute_command_phase2_elicit_reject(
        self, harness: Any, tmp_vault: Path
    ) -> None:
        ctx_accept = _mock_ctx(elicit_action="accept", confirm=True)
        phase1 = await harness.execute_command(
            ctx_accept,
            command_id="editor:save-file",
            confirm_token=None,
            dry_run=False,
        )
        token = (phase1.data or {}).get("confirm_token")
        if token is None:
            pytest.skip("Phase 1 did not issue a token (REST unavailable)")

        ctx_reject = _mock_ctx(elicit_action="reject", confirm=False)
        result = await harness.execute_command(
            ctx_reject,
            command_id="editor:save-file",
            confirm_token=token,
            dry_run=False,
        )
        assert result.ok is False
        assert result.error.code is ErrorCode.ELICITATION_REJECTED
