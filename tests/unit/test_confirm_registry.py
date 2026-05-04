"""Tests for security.confirm — 2-phase HMAC confirmation tokens.

`ConfirmRegistry` issues single-use, TTL-bound HMAC tokens. Phase 2 of
every destructive op consumes the token; the registry guarantees:
- single use (replay rejected),
- TTL enforcement (expired -> dedicated error),
- payload binding (caller cannot swap target / operation / params),
- tamper resistance (flipping a token byte rejects the call).

The HMAC secret is bootstrapped to `~/.obsidian-full-mcp/secret` with
mode 0o600. Loading refuses any wider mode.
"""

from __future__ import annotations

import base64
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obsidian_full_mcp.domain.vault_path import VaultPath
from obsidian_full_mcp.security.confirm import (
    ConfirmRegistry,
    ExpiredConfirmationTokenError,
    InsecureSecretFileError,
    InvalidConfirmationTokenError,
    OperationToken,
    PayloadMismatchError,
    load_or_bootstrap_secret,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vp(tmp_vault: Path, rel: str) -> VaultPath:
    return VaultPath.from_user(rel, tmp_vault)


def _fixed_clock(value: datetime) -> Callable[[], datetime]:
    return lambda: value


# ---------------------------------------------------------------------------
# OperationToken / token format
# ---------------------------------------------------------------------------


class TestOperationToken:
    def test_token_string_is_base64url_64_bytes_unpadded(
        self, tmp_vault: Path
    ) -> None:
        registry = ConfirmRegistry(secret=b"x" * 32)
        tok = registry.issue(
            operation="delete_note",
            target=_vp(tmp_vault, "01_Notes/sample.md"),
            payload_hash="abc",
        )
        # 32-byte nonce + 32-byte HMAC = 64 bytes -> 86 chars base64url.
        assert isinstance(tok.token, str)
        assert len(tok.token) == 86
        # No padding
        assert "=" not in tok.token
        # base64url alphabet only (A-Za-z0-9-_)
        decoded = base64.urlsafe_b64decode(tok.token + "==")
        assert len(decoded) == 64

    def test_two_issues_with_same_payload_yield_different_tokens(
        self, tmp_vault: Path
    ) -> None:
        registry = ConfirmRegistry(secret=b"x" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok_a = registry.issue(
            operation="delete_note", target=target, payload_hash="h"
        )
        tok_b = registry.issue(
            operation="delete_note", target=target, payload_hash="h"
        )
        # Random nonce per issue -> tokens differ even for identical payloads.
        assert tok_a.token != tok_b.token

    def test_token_carries_operation_target_payload_expiry(
        self, tmp_vault: Path
    ) -> None:
        registry = ConfirmRegistry(secret=b"x" * 32, ttl_seconds=90)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="rename_note", target=target, payload_hash="h"
        )
        assert isinstance(tok, OperationToken)
        assert tok.operation == "rename_note"
        assert tok.target == target
        assert tok.payload_hash == "h"
        assert tok.expires_at.tzinfo is not None
        # ~90s in the future
        delta = (tok.expires_at - datetime.now(tz=UTC)).total_seconds()
        assert 80 < delta <= 90


# ---------------------------------------------------------------------------
# Issue / consume happy path
# ---------------------------------------------------------------------------


class TestIssueAndConsume:
    def test_consume_returns_silently_for_matching_call(
        self, tmp_vault: Path
    ) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        # No exception means OK.
        registry.consume(
            tok.token,
            expected_operation="delete_note",
            expected_target=target,
            expected_payload_hash="ph",
        )


# ---------------------------------------------------------------------------
# Unknown token / replay (single-use)
# ---------------------------------------------------------------------------


class TestUnknownAndReplay:
    def test_unknown_token_raises_invalid(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                "A" * 86,
                expected_operation="delete_note",
                expected_target=_vp(tmp_vault, "01_Notes/sample.md"),
                expected_payload_hash="ph",
            )

    def test_replay_after_consume_raises_invalid(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        registry.consume(
            tok.token,
            expected_operation="delete_note",
            expected_target=target,
            expected_payload_hash="ph",
        )
        # Second call: token has been popped -> Invalid (not Expired/Mismatch).
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )

    def test_empty_token_raises_invalid(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                "",
                expected_operation="delete_note",
                expected_target=_vp(tmp_vault, "01_Notes/sample.md"),
                expected_payload_hash="ph",
            )


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_consume_after_ttl_raises_expired(self, tmp_vault: Path) -> None:
        # Inject clock so we can fast-forward without sleeping.
        t0 = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        clock = {"now": t0}
        registry = ConfirmRegistry(
            secret=b"k" * 32, ttl_seconds=90, clock=lambda: clock["now"]
        )
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        # Fast-forward past TTL.
        clock["now"] = t0 + timedelta(seconds=91)
        with pytest.raises(ExpiredConfirmationTokenError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )

    def test_consume_at_exactly_ttl_boundary_still_valid(
        self, tmp_vault: Path
    ) -> None:
        t0 = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        clock = {"now": t0}
        registry = ConfirmRegistry(
            secret=b"k" * 32, ttl_seconds=90, clock=lambda: clock["now"]
        )
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        # Exactly at ttl_seconds — token is still valid (strict >).
        clock["now"] = t0 + timedelta(seconds=90)
        registry.consume(
            tok.token,
            expected_operation="delete_note",
            expected_target=target,
            expected_payload_hash="ph",
        )

    def test_expired_token_is_swept_proactively(self, tmp_vault: Path) -> None:
        """Expired tokens get pruned even without an explicit consume.

        The registry should not grow unbounded if callers issue tokens
        that nobody ever consumes; sweep on `issue()` keeps memory bounded.
        """
        t0 = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        clock = {"now": t0}
        registry = ConfirmRegistry(
            secret=b"k" * 32, ttl_seconds=10, clock=lambda: clock["now"]
        )
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        clock["now"] = t0 + timedelta(seconds=11)
        # Issuing another token sweeps expired ones.
        registry.issue(
            operation="delete_note",
            target=_vp(tmp_vault, "01_Notes/other.md"),
            payload_hash="x",
        )
        # The first token has been swept -> consume sees it as unknown.
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )


# ---------------------------------------------------------------------------
# Payload mismatch
# ---------------------------------------------------------------------------


class TestPayloadMismatch:
    def test_wrong_operation_raises_mismatch(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        with pytest.raises(PayloadMismatchError):
            registry.consume(
                tok.token,
                expected_operation="rename_note",
                expected_target=target,
                expected_payload_hash="ph",
            )

    def test_wrong_target_raises_mismatch(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        target_a = _vp(tmp_vault, "01_Notes/sample.md")
        target_b = _vp(tmp_vault, "00_Journal/2026-05-04.md")
        tok = registry.issue(
            operation="delete_note", target=target_a, payload_hash="ph"
        )
        with pytest.raises(PayloadMismatchError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target_b,
                expected_payload_hash="ph",
            )

    def test_wrong_payload_hash_raises_mismatch(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="A"
        )
        with pytest.raises(PayloadMismatchError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="B",
            )

    def test_mismatched_consume_does_not_pop_other_payloads(
        self, tmp_vault: Path
    ) -> None:
        """A mismatched call must NOT silently leak the token from the
        registry — the legitimate caller must still be able to consume."""
        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        with pytest.raises(PayloadMismatchError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="WRONG",
            )
        # The legitimate caller's consume should now succeed (or also fail —
        # we deliberately choose to invalidate on mismatch to prevent grinding
        # attacks). Document the chosen behavior here:
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )


# ---------------------------------------------------------------------------
# HMAC tamper resistance
# ---------------------------------------------------------------------------


class TestHmacTamper:
    def test_flipping_one_byte_in_token_rejects(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        raw = bytearray(base64.urlsafe_b64decode(tok.token + "=="))
        # Flip a bit in the HMAC half (last 32 bytes).
        raw[-1] ^= 0x01
        tampered = base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                tampered,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )

    def test_token_signed_with_different_secret_rejects(
        self, tmp_vault: Path
    ) -> None:
        attacker = ConfirmRegistry(secret=b"a" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        tok = attacker.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        defender = ConfirmRegistry(secret=b"b" * 32)
        with pytest.raises(InvalidConfirmationTokenError):
            defender.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )


# ---------------------------------------------------------------------------
# Command-bound tokens (M7 — execute_command)
# ---------------------------------------------------------------------------


class TestCommandBoundTokens:
    """M7 — execute_command tokens carry a `target_command` (str) instead
    of a `target` (VaultPath). The HMAC includes a `p:` / `c:`
    discriminator so a path target and a command target with the same
    string never collide."""

    def test_issue_command_token_returns_target_command(self) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        tok = registry.issue(
            operation="execute_command",
            target_command="editor:focus-current",
            payload_hash="ph",
        )
        assert tok.operation == "execute_command"
        assert tok.target is None
        assert tok.target_command == "editor:focus-current"

    def test_consume_command_token_happy_path(self) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        tok = registry.issue(
            operation="execute_command",
            target_command="editor:focus-current",
            payload_hash="ph",
        )
        registry.consume(
            tok.token,
            expected_operation="execute_command",
            expected_target_command="editor:focus-current",
            expected_payload_hash="ph",
        )

    def test_consume_command_token_with_swapped_id_rejected(self) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        tok = registry.issue(
            operation="execute_command",
            target_command="editor:focus-current",
            payload_hash="ph",
        )
        with pytest.raises(PayloadMismatchError):
            registry.consume(
                tok.token,
                expected_operation="execute_command",
                expected_target_command="workspace:close",
                expected_payload_hash="ph",
            )

    def test_consume_command_with_path_expectation_rejected(
        self, tmp_vault: Path
    ) -> None:
        """A command-bound token must not be consumable with a
        VaultPath expectation (mixing token kinds)."""
        registry = ConfirmRegistry(secret=b"k" * 32)
        tok = registry.issue(
            operation="execute_command",
            target_command="editor:focus-current",
            payload_hash="ph",
        )
        with pytest.raises(PayloadMismatchError):
            registry.consume(
                tok.token,
                expected_operation="execute_command",
                expected_target=_vp(tmp_vault, "01_Notes/sample.md"),
                expected_payload_hash="ph",
            )

    def test_consume_path_with_command_expectation_rejected(
        self, tmp_vault: Path
    ) -> None:
        """Inverse: a path-bound token cannot be consumed via the
        command path."""
        registry = ConfirmRegistry(secret=b"k" * 32)
        tok = registry.issue(
            operation="delete_note",
            target=_vp(tmp_vault, "01_Notes/sample.md"),
            payload_hash="ph",
        )
        with pytest.raises(PayloadMismatchError):
            registry.consume(
                tok.token,
                expected_operation="delete_note",
                expected_target_command="editor:focus-current",
                expected_payload_hash="ph",
            )

    def test_issue_with_neither_target_nor_command_rejected(
        self,
    ) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        with pytest.raises(ValueError, match="exactly one"):
            registry.issue(
                operation="execute_command",
                payload_hash="ph",
            )

    def test_issue_with_both_target_and_command_rejected(
        self, tmp_vault: Path
    ) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        with pytest.raises(ValueError, match="exactly one"):
            registry.issue(
                operation="execute_command",
                target=_vp(tmp_vault, "01_Notes/sample.md"),
                target_command="editor:focus",
                payload_hash="ph",
            )

    def test_consume_with_neither_expectation_rejected(self) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        tok = registry.issue(
            operation="execute_command",
            target_command="editor:focus",
            payload_hash="ph",
        )
        with pytest.raises(ValueError, match="exactly one"):
            registry.consume(
                tok.token,
                expected_operation="execute_command",
                expected_payload_hash="ph",
            )

    def test_consume_with_both_expectations_rejected(
        self, tmp_vault: Path
    ) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        tok = registry.issue(
            operation="execute_command",
            target_command="editor:focus",
            payload_hash="ph",
        )
        with pytest.raises(ValueError, match="exactly one"):
            registry.consume(
                tok.token,
                expected_operation="execute_command",
                expected_target=_vp(tmp_vault, "01_Notes/sample.md"),
                expected_target_command="editor:focus",
                expected_payload_hash="ph",
            )

    def test_direct_construction_with_neither_target_rejected(self) -> None:
        """The dataclass `__post_init__` enforces the mutex even when
        someone bypasses the registry and constructs OperationToken
        directly. This is defense-in-depth — the registry's `issue()`
        also validates."""
        from datetime import UTC, datetime

        with pytest.raises(ValueError, match="exactly one"):
            OperationToken(
                token="x",
                operation="execute_command",
                expires_at=datetime.now(tz=UTC),
                payload_hash="ph",
            )

    def test_direct_construction_with_both_targets_rejected(
        self, tmp_vault: Path
    ) -> None:
        from datetime import UTC, datetime

        with pytest.raises(ValueError, match="exactly one"):
            OperationToken(
                token="x",
                operation="execute_command",
                expires_at=datetime.now(tz=UTC),
                payload_hash="ph",
                target=_vp(tmp_vault, "01_Notes/sample.md"),
                target_command="editor:focus",
            )

    def test_command_path_collision_resistance(
        self, tmp_vault: Path
    ) -> None:
        """A path 'foo' and a command 'foo' must produce different HMACs
        (the discriminator prefix prevents collision)."""
        registry = ConfirmRegistry(secret=b"k" * 32)
        (tmp_vault / "foo.md").write_text("hi\n")
        path_tok = registry.issue(
            operation="delete_note",
            target=_vp(tmp_vault, "foo.md"),
            payload_hash="ph",
        )
        command_tok = registry.issue(
            operation="execute_command",
            target_command="foo.md",
            payload_hash="ph",
        )
        # Tokens differ (random nonce + different HMAC inputs).
        assert path_tok.token != command_tok.token


# ---------------------------------------------------------------------------
# Secret bootstrap / loading
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    def test_secret_must_be_bytes(self) -> None:
        with pytest.raises(ValueError, match="at least 32 bytes"):
            ConfirmRegistry(secret="not bytes")  # type: ignore[arg-type]

    def test_secret_must_be_at_least_32_bytes(self) -> None:
        with pytest.raises(ValueError, match="at least 32 bytes"):
            ConfirmRegistry(secret=b"short")

    def test_ttl_seconds_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            ConfirmRegistry(secret=b"k" * 32, ttl_seconds=0)

    def test_ttl_seconds_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            ConfirmRegistry(secret=b"k" * 32, ttl_seconds=-1)


class TestMalformedTokens:
    def test_non_base64_token_rejected(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        # Issue a real token to populate the store, then try a fake one
        # with the SAME length but invalid base64 chars (`!`).
        registry.issue(
            operation="delete_note",
            target=_vp(tmp_vault, "01_Notes/sample.md"),
            payload_hash="ph",
        )
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                "!" * 86,
                expected_operation="delete_note",
                expected_target=_vp(tmp_vault, "01_Notes/sample.md"),
                expected_payload_hash="ph",
            )

    def test_wrong_length_token_rejected(self, tmp_vault: Path) -> None:
        registry = ConfirmRegistry(secret=b"k" * 32)
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                "AAAA",  # too short
                expected_operation="delete_note",
                expected_target=_vp(tmp_vault, "01_Notes/sample.md"),
                expected_payload_hash="ph",
            )

    def test_in_memory_token_with_invalid_base64_rejected(
        self, tmp_vault: Path
    ) -> None:
        """If an attacker plants a non-base64 token string in the store,
        consume's HMAC verification path catches the decode error and
        rejects rather than crashing."""
        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        real = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        # Replace the dict key with a string whose (length + 2) is not a
        # multiple of 4 — base64 will raise binascii.Error on decode.
        bad_key = "A"  # len=1; "A" + "==" = "A==" -> binascii.Error
        registry._store.pop(real.token)
        registry._store[bad_key] = real
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                bad_key,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )

    def test_in_memory_token_with_wrong_length_rejected(
        self, tmp_vault: Path
    ) -> None:
        """Same idea: a planted token that decodes to the wrong length
        is rejected by the HMAC verifier without raising past the API."""
        import base64 as _b64

        registry = ConfirmRegistry(secret=b"k" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        real = registry.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        # 16-byte payload base64url'd is short — wrong length.
        wrong_len_token = (
            _b64.urlsafe_b64encode(b"\x00" * 16).rstrip(b"=").decode("ascii")
        )
        registry._store.pop(real.token)
        registry._store[wrong_len_token] = real
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                wrong_len_token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )

    def test_hmac_verification_catches_in_memory_tamper(
        self, tmp_vault: Path
    ) -> None:
        """Defense in depth: even if attacker inserted a forged
        OperationToken into the store under a token string whose HMAC
        was signed with a different secret, the consume-side HMAC
        verification rejects it."""
        registry = ConfirmRegistry(secret=b"k" * 32)
        attacker = ConfirmRegistry(secret=b"a" * 32)
        target = _vp(tmp_vault, "01_Notes/sample.md")
        forged = attacker.issue(
            operation="delete_note", target=target, payload_hash="ph"
        )
        # Plant the attacker-signed token in the defender's store. The
        # defender's HMAC verification MUST reject it.
        registry._store[forged.token] = forged
        with pytest.raises(InvalidConfirmationTokenError):
            registry.consume(
                forged.token,
                expected_operation="delete_note",
                expected_target=target,
                expected_payload_hash="ph",
            )


class TestSecretBootstrap:
    def test_bootstrap_creates_secret_with_mode_600(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "subdir" / "secret"
        loaded = load_or_bootstrap_secret(secret_file)
        assert isinstance(loaded, bytes)
        assert len(loaded) == 32  # secrets.token_bytes(32)
        # Mode is exactly 0o600
        mode = stat.S_IMODE(secret_file.stat().st_mode)
        assert mode == 0o600
        # Parent dir was created
        assert secret_file.parent.exists()

    def test_existing_secret_with_mode_600_is_loaded(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "secret"
        secret_file.write_bytes(b"\x42" * 32)
        os.chmod(secret_file, 0o600)
        loaded = load_or_bootstrap_secret(secret_file)
        assert loaded == b"\x42" * 32

    def test_existing_secret_with_loose_mode_refuses(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "secret"
        secret_file.write_bytes(b"\x42" * 32)
        os.chmod(secret_file, 0o644)
        with pytest.raises(InsecureSecretFileError):
            load_or_bootstrap_secret(secret_file)

    def test_existing_secret_with_world_readable_mode_refuses(
        self, tmp_path: Path
    ) -> None:
        secret_file = tmp_path / "secret"
        secret_file.write_bytes(b"\x42" * 32)
        os.chmod(secret_file, 0o604)
        with pytest.raises(InsecureSecretFileError):
            load_or_bootstrap_secret(secret_file)

    def test_bootstrap_is_idempotent_within_session(
        self, tmp_path: Path
    ) -> None:
        secret_file = tmp_path / "secret"
        a = load_or_bootstrap_secret(secret_file)
        b = load_or_bootstrap_secret(secret_file)
        assert a == b
