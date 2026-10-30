"""Unit tests for utils/fiscal.py — no DB required."""
from datetime import date

import pytest

from utils.fiscal import fiscal_period, get_fiscal_year


class TestGetFiscalYear:
    def test_before_april_is_same_year(self):
        assert get_fiscal_year(date(2025, 3, 31)) == 2025

    def test_april_starts_new_fy(self):
        assert get_fiscal_year(date(2025, 4, 1)) == 2026

    def test_december_mid_year(self):
        assert get_fiscal_year(date(2024, 12, 15)) == 2025

    def test_jan_1_is_current_fy(self):
        assert get_fiscal_year(date(2026, 1, 1)) == 2026


class TestFiscalPeriod:
    def test_q1_april(self):
        fy, fq, fm = fiscal_period(date(2025, 4, 1))
        assert fy == 2026
        assert fq == 1
        assert fm == 1

    def test_q3_october(self):
        fy, fq, fm = fiscal_period(date(2024, 10, 15))
        assert fy == 2025
        assert fq == 3

    def test_q4_march(self):
        fy, fq, fm = fiscal_period(date(2025, 3, 31))
        assert fy == 2025
        assert fq == 4
        assert fm == 12

    def test_fiscal_month_counts_from_april(self):
        # April = FM1, May = FM2, ... March = FM12
        _, _, fm_april = fiscal_period(date(2025, 4, 1))
        _, _, fm_march = fiscal_period(date(2026, 3, 1))
        assert fm_april == 1
        assert fm_march == 12

    def test_known_period(self):
        fy, fq, fm = fiscal_period(date(2024, 12, 15))
        assert (fy, fq, fm) == (2025, 3, 9)
