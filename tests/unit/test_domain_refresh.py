"""Tests for the refresh contract domain module (vault-refresh v1)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from obsidian_hardened_mcp.domain.refresh import (
    POLICIES,
    InvalidContractError,
    RefreshContract,
    compute_due,
    parse_contract,
    parse_interval,
)


class TestParseInterval:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("7d", (7, "d")), ("2w", (2, "w")), ("1m", (1, "m")), ("1y", (1, "y"))],
    )
    def test_valid(self, raw: str, expected: tuple[int, str]) -> None:
        assert parse_interval(raw) == expected

    @pytest.mark.parametrize("raw", ["1x", "d7", "0d", "-1m", "1.5m", "", "m", "12"])
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(InvalidContractError):
            parse_interval(raw)

    def test_huge_magnitude_raises(self) -> None:
        # Reproduces the OverflowError previously escaping compute_due().
        with pytest.raises(InvalidContractError):
            parse_interval("99999999d")

    @pytest.mark.parametrize(
        "raw", ["36500d", "5200w", "1200m", "100y"]
    )
    def test_boundary_magnitude_accepted(self, raw: str) -> None:
        n, unit = parse_interval(raw)
        assert (n, unit) == (int(raw[:-1]), raw[-1])

    @pytest.mark.parametrize(
        "raw", ["36501d", "5201w", "1201m", "101y"]
    )
    def test_boundary_magnitude_rejected(self, raw: str) -> None:
        with pytest.raises(InvalidContractError, match="magnitude exceeds max"):
            parse_interval(raw)


class TestComputeDue:
    def test_days(self) -> None:
        assert compute_due(date(2026, 7, 5), "7d") == date(2026, 7, 12)

    def test_weeks(self) -> None:
        assert compute_due(date(2026, 7, 5), "2w") == date(2026, 7, 19)

    def test_months_simple(self) -> None:
        assert compute_due(date(2026, 7, 5), "1m") == date(2026, 8, 5)

    def test_month_end_clamp(self) -> None:
        # 31 janvier + 1 mois -> 28 février (2026 non bissextile)
        assert compute_due(date(2026, 1, 31), "1m") == date(2026, 2, 28)

    def test_month_end_clamp_leap(self) -> None:
        assert compute_due(date(2028, 1, 31), "1m") == date(2028, 2, 29)

    def test_year_rollover(self) -> None:
        assert compute_due(date(2026, 11, 15), "3m") == date(2027, 2, 15)

    def test_years_leap_clamp(self) -> None:
        assert compute_due(date(2028, 2, 29), "1y") == date(2029, 2, 28)


class TestParseContract:
    def test_no_frontmatter_is_no_contract(self) -> None:
        assert parse_contract(None) is None

    def test_unrelated_frontmatter_is_no_contract(self) -> None:
        assert parse_contract({"title": "Hello"}) is None

    def test_minimal_contract_defaults_to_flag(self) -> None:
        c = parse_contract({"refresh_every": "1m", "refresh_last": date(2026, 7, 5)})
        assert c == RefreshContract(
            policy="flag", every="1m", last=date(2026, 7, 5), prompt=None
        )

    def test_full_contract(self) -> None:
        c = parse_contract(
            {
                "refresh_policy": "on_read",
                "refresh_every": "2w",
                "refresh_last": "2026-07-05",  # ISO string accepté
                "refresh_prompt": "Re-check prices.",
            }
        )
        assert c is not None
        assert c.policy == "on_read"
        assert c.last == date(2026, 7, 5)
        assert c.prompt == "Re-check prices."

    def test_partial_contract_raises(self) -> None:
        with pytest.raises(InvalidContractError, match="refresh_last"):
            parse_contract({"refresh_every": "1m"})
        with pytest.raises(InvalidContractError, match="refresh_every"):
            parse_contract({"refresh_last": "2026-07-05"})

    def test_unknown_policy_raises(self) -> None:
        with pytest.raises(InvalidContractError, match="refresh_policy"):
            parse_contract(
                {
                    "refresh_policy": "yolo",
                    "refresh_every": "1m",
                    "refresh_last": "2026-07-05",
                }
            )

    def test_bad_date_raises(self) -> None:
        with pytest.raises(InvalidContractError, match="refresh_last"):
            parse_contract({"refresh_every": "1m", "refresh_last": "someday"})

    def test_policies_constant(self) -> None:
        assert POLICIES == ("auto", "on_read", "flag")

    def test_datetime_value_is_coerced_to_date(self) -> None:
        c = parse_contract(
            {"refresh_every": "1m", "refresh_last": datetime(2026, 7, 5, 12, 30)}
        )
        assert c is not None
        assert c.last == date(2026, 7, 5)

    def test_non_date_non_str_last_raises(self) -> None:
        with pytest.raises(InvalidContractError, match="refresh_last"):
            parse_contract({"refresh_every": "1m", "refresh_last": 20260705})
