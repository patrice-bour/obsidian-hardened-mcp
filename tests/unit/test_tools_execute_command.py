"""Tests for tools.destructive.execute_command — REST-only 2-phase tool.

`execute_command` is the only tool that REQUIRES the Local REST API
plugin. Without it (no `rest_token` configured, or detector reports
unavailable) the call short-circuits with `REST_UNAVAILABLE`. Otherwise
the same 2-phase HMAC protocol as `delete_note` applies, with the
token bound to the **command id** instead of a vault path.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.domain.results import ErrorCode
from obsidian_full_mcp.rest.client import (
    RestAuthError,
    RestError,
    RestUnavailableError,
)
from obsidian_full_mcp.rest.detector import RestAvailabilityDetector
from obsidian_full_mcp.security.audit_logger import AuditLogger
from obsidian_full_mcp.security.confirm import ConfirmRegistry
from obsidian_full_mcp.tools.destructive import execute_command

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRestClient:
    """Pretend to be the `RestClient`; record calls; script responses."""

    def __init__(
        self,
        *,
        health_responses: list[bool | Exception] | None = None,
        execute_responses: list[dict | Exception] | None = None,
    ) -> None:
        self._health_responses = list(health_responses or [True])
        self._execute_responses = list(execute_responses or [{"ok": True}])
        self.execute_calls: list[str] = []

    def health_check(self) -> bool:
        if not self._health_responses:
            raise AssertionError("FakeRestClient ran out of health responses")
        result = self._health_responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def execute_command(self, command_id: str) -> dict:
        self.execute_calls.append(command_id)
        if not self._execute_responses:
            raise AssertionError("FakeRestClient ran out of execute responses")
        result = self._execute_responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_vault: Path, tmp_path: Path) -> AppConfig:
    return AppConfig(vault_root=tmp_vault, audit_dir=tmp_path / "audit")


@pytest.fixture
def audit(config: AppConfig) -> AuditLogger:
    return AuditLogger(audit_dir=config.audit_dir)


@pytest.fixture
def registry() -> ConfirmRegistry:
    return ConfirmRegistry(secret=b"k" * 32)


@pytest.fixture
def clocked_registry() -> Iterator[tuple[ConfirmRegistry, dict]]:
    state = {"now": datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)}
    reg = ConfirmRegistry(
        secret=b"k" * 32, ttl_seconds=90, clock=lambda: state["now"]
    )
    yield reg, state


def _detector_for(client: _FakeRestClient, *, available: bool) -> RestAvailabilityDetector:
    """Build a detector wrapping the fake client. We pre-seed the cache
    rather than relying on the live probe so tests stay deterministic."""
    detector = RestAvailabilityDetector(client, ttl_seconds=60)
    if available:
        detector._cached = True
        detector._checked_at = datetime.now(tz=UTC)
    else:
        detector._cached = False
        detector._checked_at = datetime.now(tz=UTC)
    return detector


def _last_audit(audit_dir: Path) -> dict:
    files = sorted(audit_dir.glob("*.jsonl"))
    assert files, "no audit log file"
    lines = files[-1].read_text().splitlines()
    assert lines, "no audit lines"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# REST availability gating
# ---------------------------------------------------------------------------


class TestRestAvailability:
    def test_no_client_no_detector_returns_rest_unavailable(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        result = execute_command(
            config,
            audit,
            registry,
            None,
            None,
            command_id="editor:focus",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.REST_UNAVAILABLE

    def test_detector_unavailable_returns_rest_unavailable(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=False)
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.REST_UNAVAILABLE
        # No REST call attempted.
        assert client.execute_calls == []


# ---------------------------------------------------------------------------
# command_id validation
# ---------------------------------------------------------------------------


class TestCommandIdValidation:
    def test_empty_command_id_returns_invalid_path(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_command_id_with_null_byte_rejected(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus\x00",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH

    def test_command_id_with_record_separator_rejected(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        """M7.5 — `\\x1e` is the HMAC field separator; rejecting it
        in command_id keeps the encoding unambiguous."""
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:foo\x1ebar",
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.INVALID_PATH


# ---------------------------------------------------------------------------
# dry_run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_preview_without_token_or_call(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            dry_run=True,
        )
        assert result.ok
        assert result.dry_run is True
        assert result.data is not None
        assert "confirm_token" not in result.data
        assert result.data["command_id"] == "editor:focus"
        assert result.data["would_execute"] is True
        # No REST call.
        assert client.execute_calls == []

    def test_dry_run_emits_destructive_dry_run_audit(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            dry_run=True,
        )
        record = _last_audit(config.audit_dir)
        assert record["tool"] == "execute_command"
        assert record["op_kind"] == "destructive"
        assert record["dry_run"] is True
        assert record["outcome"] == "success"


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


class TestPhase1:
    def test_phase1_returns_token_and_preview(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus-current",
        )
        assert result.ok
        assert result.dry_run is True
        assert result.data is not None
        assert "confirm_token" in result.data
        assert len(result.data["confirm_token"]) == 86
        assert "expires_at" in result.data
        assert result.data["command_id"] == "editor:focus-current"
        # No REST call yet.
        assert client.execute_calls == []


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------


class TestPhase2:
    def test_phase2_executes_command(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient(execute_responses=[{"executed": True}])
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        commit = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert commit.ok
        assert commit.dry_run is False
        assert commit.audit_id is not None
        assert client.execute_calls == ["editor:focus"]
        assert commit.data is not None
        assert commit.data["result"] == {"executed": True}

    def test_phase2_emits_destructive_success_audit(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        record = _last_audit(config.audit_dir)
        assert record["tool"] == "execute_command"
        assert record["op_kind"] == "destructive"
        assert record["dry_run"] is False
        assert record["outcome"] == "success"
        # No file path -> snapshot_id stays null.
        assert record["snapshot_id"] is None

    def test_phase2_with_swapped_command_id_returns_payload_mismatch(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="workspace:close",  # different
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.PAYLOAD_MISMATCH
        assert client.execute_calls == []

    def test_phase2_with_expired_token_returns_expired(
        self,
        config: AppConfig,
        audit: AuditLogger,
        clocked_registry: tuple[ConfirmRegistry, dict],
    ) -> None:
        registry, state = clocked_registry
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        state["now"] = state["now"] + timedelta(seconds=91)
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.EXPIRED_CONFIRMATION_TOKEN
        assert client.execute_calls == []

    def test_replay_with_rest_down_returns_invalid_not_unavailable(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        """M7.5 — when phase 2 is replayed AND the REST endpoint went
        down between phases, the security signal (INVALID token) MUST
        win over the transient REST_UNAVAILABLE."""
        client = _FakeRestClient()
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        # First commit succeeds.
        execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        # Now flip the detector to unavailable AND replay.
        detector._cached = False
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        # INVALID wins over REST_UNAVAILABLE.
        assert result.error.code is ErrorCode.INVALID_CONFIRMATION_TOKEN

    def test_replay_after_consume_rejected(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient(execute_responses=[{"ok": True}, {"ok": True}])
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        ok = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert ok.ok
        replay = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert not replay.ok
        assert replay.error is not None
        assert replay.error.code is ErrorCode.INVALID_CONFIRMATION_TOKEN


# ---------------------------------------------------------------------------
# REST mid-call failures (after token consumed)
# ---------------------------------------------------------------------------


class TestRestFailureMidExecute:
    def test_rest_unavailable_during_execute_returns_rest_unavailable(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient(
            execute_responses=[RestUnavailableError("nope")]
        )
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.REST_UNAVAILABLE
        # Audit records the failed destructive op.
        record = _last_audit(config.audit_dir)
        assert record["tool"] == "execute_command"
        assert record["op_kind"] == "destructive"
        assert record["outcome"] == "failure"
        assert record["dry_run"] is False

    def test_rest_auth_error_during_execute_returns_rest_auth_failed(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient(execute_responses=[RestAuthError("401")])
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.REST_AUTH_FAILED

    def test_rest_error_during_execute_returns_rest_error(
        self,
        config: AppConfig,
        audit: AuditLogger,
        registry: ConfirmRegistry,
    ) -> None:
        client = _FakeRestClient(execute_responses=[RestError("500")])
        detector = _detector_for(client, available=True)
        first = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
        )
        token = first.data["confirm_token"]  # type: ignore[index]
        result = execute_command(
            config,
            audit,
            registry,
            client,  # type: ignore[arg-type]
            detector,
            command_id="editor:focus",
            confirm_token=token,
        )
        assert not result.ok
        assert result.error is not None
        assert result.error.code is ErrorCode.REST_ERROR
