"""Jurisdiction profile: which country's rules apply to a tenant.

Onsumm was built for Indian companies paying in rupees, so GSTIN checks, PAN,
TDS, IFSC bank accounts, IMPS, the April fiscal year, `en-IN` formatting and the
₹ sign were simply assumed everywhere. A tenant outside India (the crypto rail's
Lumen Labs, Singapore, books in USD) needs none of that, and some of it actively
gets in the way: a foreign vendor can never become "GST verified", so a payment
guardrail that requires it blocks every payment.

This module is the one place that answers "does this rule apply to this
tenant?". Every India-specific check asks it instead of assuming.

    profile      'india' (today's behaviour, the default) or 'international'
    packs        India-specific rule packs, each on or off:
                   gst          GSTIN capture + verification, CGST/SGST/IGST split,
                                HSN/SAC, place of supply, GST tax-type checks
                   pan          PAN capture + PAN checks
                   tds_india    Indian TDS sections, deduction and 26Q
                   bank_india   IFSC bank accounts, penny-drop verification
                   imps         the IMPS payment rail
                   msme         MSME / Udyam 45-day payment rule
    rails        payment rails offered: IMPS for India; USDC (if licensed) abroad
    money/dates  base_currency, locale, timezone, fiscal_year_start_month
    labels       what to call the tax ID and the indirect tax on screens

Defaults come from `dim_tenants.jurisdiction_profile` (+ country_code,
base_currency, timezone). Any single value can be overridden per tenant in
`dim_tenants.settings_jsonb -> 'jurisdiction'`, e.g. an international tenant
that does have Indian vendors and wants GST checks back on:

    {"jurisdiction": {"packs": {"gst": true}}}

SQL callers (pipeline workers that sweep many tenants at once) use
`pack_enabled_sql()` so the rule is identical in Python and in SQL.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from psycopg2.extras import RealDictCursor

PACKS = ("gst", "pan", "tds_india", "bank_india", "imps", "msme")

# Country → (locale, timezone, fiscal year start month, tax-ID label, indirect-tax label)
_COUNTRY_DEFAULTS: dict[str, tuple[str, str, int, str, str]] = {
    "IN": ("en-IN", "Asia/Kolkata", 4, "GSTIN", "GST"),
    "SG": ("en-SG", "Asia/Singapore", 1, "UEN / GST reg. no.", "GST"),
    "AE": ("en-AE", "Asia/Dubai", 1, "TRN", "VAT"),
    "US": ("en-US", "America/New_York", 1, "EIN", "Sales tax"),
    "GB": ("en-GB", "Europe/London", 4, "VAT number", "VAT"),
}
_DEFAULT_INTL = ("en-US", "UTC", 1, "Tax ID", "Tax")


@dataclass(frozen=True)
class Jurisdiction:
    profile: str = "india"
    country_code: str = "IN"
    base_currency: str = "INR"
    is_resident_india: bool = True
    locale: str = "en-IN"
    timezone: str = "Asia/Kolkata"
    fiscal_year_start_month: int = 4
    tax_id_label: str = "GSTIN"
    indirect_tax_label: str = "GST"
    packs: dict = field(default_factory=lambda: {p: True for p in PACKS})
    rails: tuple = ("IMPS",)

    def has(self, pack: str) -> bool:
        return bool(self.packs.get(pack, False))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rails"] = list(self.rails)
        return d


INDIA = Jurisdiction()


def build(row: Optional[dict]) -> Jurisdiction:
    """A Jurisdiction from a dim_tenants row (any missing column → India)."""
    row = row or {}
    profile = (row.get("jurisdiction_profile") or "india").strip().lower()
    country = (row.get("country_code") or ("IN" if profile == "india" else "")).strip().upper() or "IN"
    india = profile == "india"
    locale, tz, fy, tax_id, ind_tax = _COUNTRY_DEFAULTS.get(country, _DEFAULT_INTL)
    if india:
        locale, tz, fy, tax_id, ind_tax = _COUNTRY_DEFAULTS["IN"]
    packs = {p: india for p in PACKS}
    rails = ["IMPS"] if india else ["USDC"]

    settings = row.get("settings_jsonb") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except ValueError:
            settings = {}
    over = (settings or {}).get("jurisdiction") or {}
    for p, v in (over.get("packs") or {}).items():
        if p in PACKS:
            packs[p] = bool(v)
    if "rails" in over and isinstance(over["rails"], list):
        rails = [str(r).upper() for r in over["rails"]]
    elif not packs["imps"] and "IMPS" in rails:
        rails.remove("IMPS")
    elif packs["imps"] and "IMPS" not in rails:
        rails.insert(0, "IMPS")

    resident = row.get("is_resident_india")
    # dim_tenants.timezone defaults to Asia/Kolkata for every row, so on a
    # non-Indian tenant that value is the column default, not a choice.
    row_tz = row.get("timezone")
    if row_tz and not (not india and row_tz == "Asia/Kolkata"):
        tz = row_tz

    return Jurisdiction(
        profile=profile,
        country_code=country,
        base_currency=(row.get("base_currency") or ("INR" if india else "USD")).strip().upper(),
        is_resident_india=bool(resident) if resident is not None else india,
        locale=over.get("locale") or locale,
        timezone=over.get("timezone") or tz,
        fiscal_year_start_month=int(over.get("fiscal_year_start_month") or fy),
        tax_id_label=over.get("tax_id_label") or tax_id,
        indirect_tax_label=over.get("indirect_tax_label") or ind_tax,
        packs=packs,
        rails=tuple(rails),
    )


# ── cached lookup ─────────────────────────────────────────────────────────────
_TTL = 60.0
_cache: dict[str, tuple[float, Jurisdiction]] = {}
_lock = threading.Lock()


def _load(tenant_id: str) -> Jurisdiction:
    from ..config import get_conn, put_conn

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM dim_tenants WHERE tenant_id = %s", (tenant_id,))
            row = cur.fetchone()
        return build(dict(row) if row else None)
    except Exception:
        conn.rollback()
        return INDIA
    finally:
        put_conn(conn)


def get_jurisdiction(tenant_id: Optional[str]) -> Jurisdiction:
    """The tenant's jurisdiction, cached for a minute. India when unknown."""
    if not tenant_id:
        return INDIA
    now = time.monotonic()
    with _lock:
        hit = _cache.get(str(tenant_id))
        if hit and now - hit[0] < _TTL:
            return hit[1]
    j = _load(str(tenant_id))
    with _lock:
        _cache[str(tenant_id)] = (now, j)
    return j


def jurisdiction_for_cursor(cur, tenant_id: str) -> Jurisdiction:
    """Same, read on the caller's cursor (inside its transaction), uncached."""
    cur.execute("SELECT * FROM dim_tenants WHERE tenant_id = %s", (tenant_id,))
    row = cur.fetchone()
    if row is not None and not isinstance(row, dict):
        cols = [d[0] for d in cur.description]
        row = dict(zip(cols, row))
    return build(row)


def has_pack(tenant_id: Optional[str], pack: str) -> bool:
    return get_jurisdiction(tenant_id).has(pack)


def invalidate(tenant_id: Optional[str] = None) -> None:
    with _lock:
        if tenant_id:
            _cache.pop(str(tenant_id), None)
        else:
            _cache.clear()


def pack_enabled_sql(pack: str, tenant_col: str) -> str:
    """SQL predicate: true when `pack` applies to the row's tenant.

    Mirrors build(): the per-tenant override wins, else the profile default
    (on for 'india', off otherwise). Tenants with no dim_tenants row count as
    India.

    `tenant_col` must be qualified (`s.tenant_id`, `dim_suppliers.tenant_id`):
    the predicate is a subquery over dim_tenants, which has its own tenant_id,
    so a bare `tenant_id` would bind to the inner table and always be true.
    """
    if pack not in PACKS:
        raise ValueError(f"unknown pack {pack!r}")
    if "." not in tenant_col:
        raise ValueError(f"tenant_col must be table-qualified, got {tenant_col!r}")
    return (
        f"COALESCE((SELECT COALESCE((_jur.settings_jsonb->'jurisdiction'->'packs'->>'{pack}')::boolean,"
        f" COALESCE(_jur.jurisdiction_profile, 'india') = 'india')"
        f" FROM dim_tenants _jur WHERE _jur.tenant_id = {tenant_col}), true)"
    )
