"""Two-phase confirmation registry for destructive operations.

Destructive tools (`delete_note`, `rename_note`, `move_note`) follow a
two-phase protocol:

    Phase 1: caller invokes the tool without `confirm_token`. The tool
    issues an `OperationToken` (HMAC-signed) bound to the FULL phase-2
    payload (operation, target, params hash, expiry). It does NOT touch
    the disk.

    Phase 2: caller invokes the tool again, passing the same token. The
    registry verifies the token is known, fresh, and bound to the SAME
    payload as phase 1, then consumes it (single-use). Only then does
    the tool perform the destructive write.

This makes a single hallucinated tool call by an upstream LLM unable to
mutate the vault on the first try — the LLM would have to call the same
tool twice with the matching token, which it has no way to fabricate
without the secret.

The HMAC secret lives at `~/.obsidian-power-mcp/secret` (mode 0o600).
We refuse to load it under any wider mode — a permission slip would let
local users forge tokens.

Storage is in-memory by design. Restarting the server invalidates all
phase-1 tokens; given the 90s TTL that is acceptable.
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets as _secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from obsidian_power_mcp.domain.vault_path import VaultPath

OperationName = Literal["delete_note", "rename_note", "move_note", "batch"]

_NONCE_BYTES = 32
_HMAC_BYTES = 32
_SECRET_BYTES = 32
_FIELD_SEP = b"\x1e"  # ASCII record separator — won't appear in our fields.


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfirmationError(Exception):
    """Base class for confirmation registry errors."""


class InvalidConfirmationTokenError(ConfirmationError):
    """Token is unknown, malformed, or its HMAC fails verification."""


class ExpiredConfirmationTokenError(ConfirmationError):
    """Token was valid at issue time but has passed its TTL."""


class PayloadMismatchError(ConfirmationError):
    """Token is valid but bound to a different operation/target/params."""


class InsecureSecretFileError(Exception):
    """Secret file exists with permissions wider than 0o600."""


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OperationToken:
    """A single-use confirmation token bound to a specific destructive call."""

    token: str
    operation: OperationName
    target: VaultPath
    expires_at: datetime
    payload_hash: str


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class ConfirmRegistry:
    """Issue and consume single-use HMAC confirmation tokens.

    The registry is in-memory only; instantiate ONE per server. Pass
    explicit instances into the destructive tools (don't use module-level
    state — that breaks isolation in tests and parallel runs).
    """

    def __init__(
        self,
        secret: bytes,
        *,
        ttl_seconds: int = 90,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) < _SECRET_BYTES:
            raise ValueError(
                f"secret must be at least {_SECRET_BYTES} bytes; got "
                f"{len(secret) if isinstance(secret, bytes) else type(secret).__name__}"
            )
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._secret = secret
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._store: dict[str, OperationToken] = {}

    def issue(
        self,
        *,
        operation: OperationName,
        target: VaultPath,
        payload_hash: str,
    ) -> OperationToken:
        """Issue a fresh single-use token for a phase-2 destructive call."""
        self._sweep_expired()
        now = self._clock()
        expires_at = now + self._ttl
        nonce = _secrets.token_bytes(_NONCE_BYTES)
        mac = self._compute_hmac(
            operation=operation,
            target=target,
            payload_hash=payload_hash,
            expires_at=expires_at,
            nonce=nonce,
        )
        token_str = base64.urlsafe_b64encode(nonce + mac).rstrip(b"=").decode(
            "ascii"
        )
        op_token = OperationToken(
            token=token_str,
            operation=operation,
            target=target,
            expires_at=expires_at,
            payload_hash=payload_hash,
        )
        self._store[token_str] = op_token
        return op_token

    def consume(
        self,
        token: str,
        *,
        expected_operation: OperationName,
        expected_target: VaultPath,
        expected_payload_hash: str,
    ) -> None:
        """Verify and consume `token`. Single-use.

        Raises:
            InvalidConfirmationTokenError: token unknown / malformed / tampered.
            ExpiredConfirmationTokenError: token past its TTL.
            PayloadMismatchError: token bound to different operation/target/payload.
        """
        if not token:
            raise InvalidConfirmationTokenError("empty token")
        # Single-use: pop on consume so a replay (or a payload-mismatched
        # second attempt) cannot reuse the entry.
        stored = self._store.pop(token, None)
        if stored is None:
            raise InvalidConfirmationTokenError("unknown or already consumed token")

        # Expiry check: the popped entry tells us the issue-time expiry.
        if self._clock() > stored.expires_at:
            raise ExpiredConfirmationTokenError(
                "confirmation token has expired; reissue phase 1"
            )

        # Defense in depth: re-verify HMAC against the stored payload.
        # If memory was tampered to swap the entry under a forged token,
        # this catches it.
        if not self._verify_hmac(token, stored):
            raise InvalidConfirmationTokenError(
                "token HMAC verification failed"
            )

        # Payload binding.
        if stored.operation != expected_operation:
            raise PayloadMismatchError(
                f"token bound to operation {stored.operation!r}, "
                f"got {expected_operation!r}"
            )
        if stored.target != expected_target:
            raise PayloadMismatchError(
                f"token bound to target {stored.target.relative}, "
                f"got {expected_target.relative}"
            )
        if not hmac.compare_digest(
            stored.payload_hash, expected_payload_hash
        ):
            raise PayloadMismatchError(
                "token bound to a different payload hash"
            )

    # ---------------- internals ----------------

    def _sweep_expired(self) -> None:
        now = self._clock()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            self._store.pop(k, None)

    def _compute_hmac(
        self,
        *,
        operation: OperationName,
        target: VaultPath,
        payload_hash: str,
        expires_at: datetime,
        nonce: bytes,
    ) -> bytes:
        message = (
            operation.encode("utf-8")
            + _FIELD_SEP
            + str(target.relative).encode("utf-8")
            + _FIELD_SEP
            + payload_hash.encode("utf-8")
            + _FIELD_SEP
            + expires_at.isoformat().encode("utf-8")
            + _FIELD_SEP
            + nonce
        )
        return hmac.new(self._secret, message, "sha256").digest()

    def _verify_hmac(self, token_str: str, stored: OperationToken) -> bool:
        try:
            raw = base64.urlsafe_b64decode(token_str + "==")
        except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
            return False
        if len(raw) != _NONCE_BYTES + _HMAC_BYTES:
            return False
        nonce = raw[:_NONCE_BYTES]
        mac = raw[_NONCE_BYTES:]
        expected = self._compute_hmac(
            operation=stored.operation,
            target=stored.target,
            payload_hash=stored.payload_hash,
            expires_at=stored.expires_at,
            nonce=nonce,
        )
        return hmac.compare_digest(mac, expected)


# ---------------------------------------------------------------------------
# Secret bootstrapping
# ---------------------------------------------------------------------------


def load_or_bootstrap_secret(secret_path: Path) -> bytes:
    """Load the HMAC secret, generating one on first boot.

    The file MUST be mode 0o600 (owner read/write only). Any wider mode
    is treated as compromised and refused — a local attacker with read
    access to the file could forge tokens.
    """
    if secret_path.exists():
        mode = stat.S_IMODE(secret_path.stat().st_mode)
        if mode != 0o600:
            raise InsecureSecretFileError(
                f"secret file {secret_path} has mode {oct(mode)}; "
                "must be 0o600. Refusing to load."
            )
        return secret_path.read_bytes()

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret = _secrets.token_bytes(_SECRET_BYTES)
    # Open with O_EXCL so we never overwrite a concurrent write, and
    # set mode at creation time (umask cannot widen it).
    fd = os.open(
        secret_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(secret)
    except BaseException:  # pragma: no cover - defensive cleanup; ENOSPC etc.
        # If anything went wrong, remove the half-written file so a retry
        # can bootstrap cleanly.
        import contextlib

        if secret_path.exists():
            with contextlib.suppress(OSError):
                secret_path.unlink()
        raise
    # Belt-and-suspenders: chmod even though O_CREAT mode set it.
    secret_path.chmod(0o600)
    return secret
