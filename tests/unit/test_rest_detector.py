"""Tests for rest.detector — TTL-cached REST availability probe.

The detector calls `RestClient.health_check()` on demand and caches
the result for `ttl_seconds`. Probes that fail are cached as
unavailable for the same TTL — the goal is to never hammer a down
endpoint inside a tight loop.

Tests inject a fake clock so we can fast-forward across the TTL
boundary without sleeping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from obsidian_full_mcp.rest.client import (
    RestAuthError,
    RestUnavailableError,
)
from obsidian_full_mcp.rest.detector import RestAvailabilityDetector


class _FakeClient:
    """Minimal stand-in for `RestClient` that records probes and lets
    tests script the response sequence. We keep the test fixture simple
    rather than building a full httpx mock for every detector test."""

    def __init__(self, sequence: list[Any]) -> None:
        self._sequence = list(sequence)
        self.probes = 0

    def health_check(self) -> bool:
        self.probes += 1
        if not self._sequence:
            raise AssertionError("FakeClient ran out of scripted responses")
        result = self._sequence.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, bool)
        return result


def _detector(
    sequence: list[Any], *, ttl: int = 60
) -> tuple[RestAvailabilityDetector, _FakeClient, dict[str, datetime]]:
    """Build a detector wrapping a fake client with an injectable clock."""
    clock_state = {"now": datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)}
    fake = _FakeClient(sequence)
    detector = RestAvailabilityDetector(
        fake,  # type: ignore[arg-type]
        ttl_seconds=ttl,
        clock=lambda: clock_state["now"],
    )
    return detector, fake, clock_state


# ---------------------------------------------------------------------------
# First call probes
# ---------------------------------------------------------------------------


class TestFirstCall:
    def test_first_call_probes(self) -> None:
        detector, fake, _ = _detector([True])
        assert fake.probes == 0
        assert detector.is_available() is True
        assert fake.probes == 1

    def test_first_call_unavailable_returns_false(self) -> None:
        detector, fake, _ = _detector([RestUnavailableError("nope")])
        assert detector.is_available() is False
        assert fake.probes == 1

    def test_first_call_auth_error_returns_false(self) -> None:
        detector, fake, _ = _detector([RestAuthError("bad token")])
        assert detector.is_available() is False
        assert fake.probes == 1


# ---------------------------------------------------------------------------
# Cache reuse within TTL
# ---------------------------------------------------------------------------


class TestCacheReuse:
    def test_second_call_within_ttl_uses_cache(self) -> None:
        detector, fake, clock = _detector([True])
        detector.is_available()
        clock["now"] = clock["now"] + timedelta(seconds=30)  # within 60s
        assert detector.is_available() is True
        assert fake.probes == 1  # no second probe

    def test_third_call_within_ttl_still_uses_cache(self) -> None:
        detector, fake, clock = _detector([True])
        detector.is_available()
        for _ in range(5):
            clock["now"] = clock["now"] + timedelta(seconds=10)
            detector.is_available()
        assert fake.probes == 1

    def test_unavailable_result_is_cached_for_full_ttl(self) -> None:
        """Fail-fast: when the endpoint is down, we don't retry on every
        call — we wait out the TTL window."""
        detector, fake, clock = _detector(
            [RestUnavailableError("down")], ttl=60
        )
        assert detector.is_available() is False
        clock["now"] = clock["now"] + timedelta(seconds=30)
        assert detector.is_available() is False
        assert fake.probes == 1


# ---------------------------------------------------------------------------
# Re-probe after TTL
# ---------------------------------------------------------------------------


class TestReprobe:
    def test_reprobes_after_ttl(self) -> None:
        detector, fake, clock = _detector([False, True])
        assert detector.is_available() is False
        clock["now"] = clock["now"] + timedelta(seconds=61)
        assert detector.is_available() is True
        assert fake.probes == 2

    def test_recovery_after_initial_failure(self) -> None:
        # Down at first; up after the TTL.
        detector, fake, clock = _detector(
            [RestUnavailableError("down"), True]
        )
        assert detector.is_available() is False
        clock["now"] = clock["now"] + timedelta(seconds=61)
        assert detector.is_available() is True
        assert fake.probes == 2

    def test_at_exact_ttl_still_uses_cache(self) -> None:
        # Strict > comparison: the re-probe happens AFTER ttl elapses.
        detector, fake, clock = _detector([True])
        detector.is_available()
        clock["now"] = clock["now"] + timedelta(seconds=60)
        detector.is_available()
        assert fake.probes == 1  # exact-equal does NOT reprobe


# ---------------------------------------------------------------------------
# invalidate()
# ---------------------------------------------------------------------------


class TestInvalidate:
    def test_invalidate_forces_reprobe(self) -> None:
        detector, fake, _ = _detector([True, False])
        assert detector.is_available() is True
        detector.invalidate()
        assert detector.is_available() is False
        assert fake.probes == 2

    def test_invalidate_before_first_call_is_noop(self) -> None:
        detector, fake, _ = _detector([True])
        detector.invalidate()  # no cache yet — no-op
        detector.is_available()
        assert fake.probes == 1


# ---------------------------------------------------------------------------
# Bad clients (defensive)
# ---------------------------------------------------------------------------


class TestUnexpectedExceptions:
    def test_unexpected_exception_treated_as_unavailable(self) -> None:
        # Even if the client raises something we didn't anticipate, the
        # detector must answer False (not propagate). Otherwise a single
        # buggy probe would crash every is_available() caller.
        detector, fake, _ = _detector([RuntimeError("surprise")])
        assert detector.is_available() is False
        assert fake.probes == 1
