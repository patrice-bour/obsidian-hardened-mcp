"""TTL-cached REST availability detector.

The Obsidian Local REST API plugin may or may not be running at any
given moment. Probing it on every tool call would be expensive (an
HTTP round-trip for each `get_vault_info`) and noisy in the logs.
The detector caches the last health-check answer for `ttl_seconds` —
60 s by default — and only re-probes when the cache expires or when
the caller explicitly invalidates it.

Failures (unavailable, auth error, anything else) are cached as
`False` for the same TTL: a down endpoint shouldn't be hammered on
every call.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class _HealthCheckable(Protocol):
    """Structural type — anything with `health_check() -> bool` works."""

    def health_check(self) -> bool: ...


class RestAvailabilityDetector:
    """Cached REST availability probe.

    `client` is anything that exposes `health_check() -> bool`. In
    production it's a `RestClient`; tests pass a fake.
    """

    def __init__(
        self,
        client: _HealthCheckable,
        *,
        ttl_seconds: int = 60,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._client = client
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._cached: bool | None = None
        self._checked_at: datetime | None = None

    def is_available(self) -> bool:
        """Returns the cached availability, re-probing only when the TTL
        has strictly elapsed since the last probe (or on first call,
        or when `invalidate()` reset the cache)."""
        if (
            self._cached is not None
            and self._checked_at is not None
            and self._clock() - self._checked_at <= self._ttl
        ):
            return self._cached

        # Probe — any exception is treated as unavailable.
        try:
            result = bool(self._client.health_check())
        except Exception:
            result = False
        self._cached = result
        self._checked_at = self._clock()
        return result

    def invalidate(self) -> None:
        """Discard the cached availability, forcing the next call to
        re-probe. Useful when a tool call observes a fresh REST failure
        and wants subsequent `is_available()` queries to reflect that."""
        self._cached = None
        self._checked_at = None
