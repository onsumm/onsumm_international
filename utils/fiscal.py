"""Indian fiscal year helpers (April → March)."""
from datetime import date


def get_fiscal_year(d: date) -> int:
    """FY ending year: April 2024 – March 2025 → 2025."""
    return d.year + 1 if d.month >= 4 else d.year


def get_fiscal_month(d: date) -> int:
    """Fiscal month 1–12 where April = 1, March = 12."""
    return d.month - 3 if d.month >= 4 else d.month + 9


def get_fiscal_quarter(d: date) -> int:
    """Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec, Q4 Jan–Mar."""
    fm = get_fiscal_month(d)
    return (fm - 1) // 3 + 1


def fiscal_period(d: date) -> tuple[int, int, int]:
    """Return (fiscal_year, fiscal_quarter, fiscal_month)."""
    fy  = get_fiscal_year(d)
    fm  = get_fiscal_month(d)
    fq  = (fm - 1) // 3 + 1
    return fy, fq, fm
