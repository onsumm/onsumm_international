"""
FX / Multi-Currency API endpoints.

All endpoints use the shared psycopg2 connection pool via _PoolConn.
INR is always the canonical reporting currency; total_amount on fact_invoices
is always INR after staging promotion.

Routes — Phase 1 (Core)
──────
GET  /api/fx/rates                        current rates for all 4 currencies
GET  /api/fx/rate?currency=USD            single currency rate
GET  /api/fx/history?currency=USD&days=30 rate history
GET  /api/fx/exposure?company_id=<uuid>   open FX exposure by currency
POST /api/bills/{bill_id}/fx-payment      record actual INR paid → back-calc effective rate
GET  /api/bills/{bill_id}/fx-payments     list FX payment records

Routes — Phase 2 (Alerts & Agent)
──────
GET  /api/fx/alerts?company_id=<uuid>     active alerts for tenant
POST /api/fx/alerts/{alert_id}/dismiss    dismiss an alert
GET  /api/fx/settings?company_id=<uuid>   FX threshold settings
PUT  /api/fx/settings                     update FX threshold settings
GET  /api/fx/report?company_id=<uuid>     FX cost analysis report data
GET  /api/fx/agent-note?bill_id=<uuid>    current agent note for a bill
GET  /api/fx/agent-briefing               exposure briefing from AI agent
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from ..core.jurisdiction import get_jurisdiction
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import TENANT_ID, get_conn, put_conn
from ..security.auth import require_tenant

router = APIRouter()

_VALID_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AED", "SGD", "AUD", "CAD"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_tenant(company_id: str | None) -> str:
    # Tenant is derived from the authenticated JWT; client company_id is ignored.
    return require_tenant()


class _PoolConn:
    def __enter__(self):
        self._conn = get_conn()
        self._conn.autocommit = False
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try:
                self._conn.rollback()
            except Exception:
                pass
            put_conn(self._conn, close=True)
        else:
            put_conn(self._conn)
        return False


# ── GET /api/fx/rates ──────────────────────────────────────────────────────────

@router.get("/api/fx/rates")
def get_fx_rates():
    """Return the most-recent rate for each tracked foreign currency."""
    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (currency_code)
                    currency_code, inr_per_unit, rate_date, source, fetched_at
                FROM dim_fx_rates
                ORDER BY currency_code, rate_date DESC
            """)
            rows = cur.fetchall()

    return {"rates": [
        {
            "currency_code": r[0].strip() if r[0] else r[0],
            "inr_per_unit":  float(r[1]) if r[1] is not None else None,
            "rate_date":     r[2].isoformat() if r[2] else None,
            "source":        r[3],
            "fetched_at":    r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]}


# ── GET /api/fx/rate?currency=USD ─────────────────────────────────────────────

@router.get("/api/fx/rate")
def get_fx_rate(currency: str = Query(...)):
    """Return the latest rate for a single currency."""
    code = currency.upper().strip()
    if code not in _VALID_CURRENCIES:
        raise HTTPException(status_code=400, detail=f"Unsupported currency: {code}")

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT currency_code, inr_per_unit, rate_date, source, fetched_at
                FROM dim_fx_rates
                WHERE currency_code = %s
                ORDER BY rate_date DESC
                LIMIT 1
            """, (code,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"No rate found for {code}")

    return {
        "currency_code": row[0].strip() if row[0] else row[0],
        "inr_per_unit":  float(row[1]) if row[1] is not None else None,
        "rate_date":     row[2].isoformat() if row[2] else None,
        "source":        row[3],
        "fetched_at":    row[4].isoformat() if row[4] else None,
    }


# ── GET /api/fx/history ────────────────────────────────────────────────────────

@router.get("/api/fx/history")
def get_fx_history(currency: str = Query(...), days: int = Query(default=30)):
    """Return daily rate history for a currency."""
    code = currency.upper().strip()
    if code not in _VALID_CURRENCIES:
        raise HTTPException(status_code=400, detail=f"Unsupported currency: {code}")
    days = max(1, min(days, 365))

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT rate_date, inr_per_unit, source, is_business_day
                FROM dim_fx_rates
                WHERE currency_code = %s
                  AND rate_date >= CURRENT_DATE - %s
                ORDER BY rate_date DESC
            """, (code, days))
            rows = cur.fetchall()

    return {
        "currency_code": code,
        "history": [
            {
                "rate_date":       r[0].isoformat() if r[0] else None,
                "inr_per_unit":    float(r[1]) if r[1] is not None else None,
                "source":          r[2],
                "is_business_day": r[3],
            }
            for r in rows
        ],
    }


# ── GET /api/fx/exposure ───────────────────────────────────────────────────────

@router.get("/api/fx/exposure")
def get_fx_exposure(company_id: str = Query(default=None)):
    """
    Open FX exposure: sum of outstanding foreign-currency bills from fact_invoices.
    Returns original_amount (foreign CCY) and current INR equivalent.
    """
    tenant = _get_tenant(company_id)
    # Exposure here is measured in INR (dim_fx_rates is INR per unit). A tenant
    # whose books are in another currency (core/jurisdiction.py) has no INR
    # exposure to report, rather than one revalued against the wrong currency.
    base_ccy = get_jurisdiction(tenant).base_currency
    if base_ccy != "INR":
        return {"exposure": [], "base_currency": base_ccy,
                "note": f"FX exposure is measured against INR; this tenant keeps its books in {base_ccy}."}
    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    fi.currency_code,
                    COUNT(*)                               AS bill_count,
                    SUM(fi.original_amount)                AS total_original,
                    SUM(fi.outstanding_amount)             AS total_inr_outstanding,
                    SUM(COALESCE(fi.inr_amount_current, fi.outstanding_amount))
                                                           AS total_inr_current,
                    SUM(COALESCE(fi.fx_gain_loss, 0))      AS total_fx_gain_loss,
                    dr.inr_per_unit                        AS current_rate
                FROM fact_invoices fi
                LEFT JOIN LATERAL (
                    SELECT inr_per_unit
                    FROM dim_fx_rates
                    WHERE currency_code = fi.currency_code
                    ORDER BY rate_date DESC
                    LIMIT 1
                ) dr ON true
                WHERE fi.tenant_id = %s
                  AND fi.currency_code != 'INR'
                  AND fi.currency_code IS NOT NULL
                  AND fi.status NOT IN ('PAID', 'VOID')
                  AND fi.original_amount IS NOT NULL
                  AND COALESCE(fi.soft_status, 'active') = 'active'
                GROUP BY fi.currency_code, dr.inr_per_unit
                ORDER BY total_inr_outstanding DESC
            """, (tenant,))
            rows = cur.fetchall()

    return {
        "exposure": [
            {
                "currency_code":         (r[0] or "").strip(),
                "bill_count":            r[1],
                "total_original":        float(r[2]) if r[2] is not None else None,
                "total_inr_outstanding": float(r[3]) if r[3] is not None else None,
                "total_inr_current":     float(r[4]) if r[4] is not None else None,
                "total_fx_gain_loss":    float(r[5]) if r[5] is not None else None,
                "current_rate":          float(r[6]) if r[6] is not None else None,
            }
            for r in rows
        ]
    }


# ── POST /api/bills/{bill_id}/fx-payment ──────────────────────────────────────

class FxPaymentRequest(BaseModel):
    actual_inr_paid: float
    payment_date: str | None = None
    bank_name: str | None = None
    bank_reference: str | None = None
    notes: str | None = None
    company_id: str | None = None


@router.post("/api/bills/{bill_id}/fx-payment")
def record_fx_payment(bill_id: str, body: FxPaymentRequest):
    """
    Record an actual INR payment for a foreign-currency bill.

    Back-calculation (all math in Decimal for precision):
      effective_rate    = actual_inr_paid / original_amount
      bank_spread       = effective_rate - mid_rate_inr_per_unit
      bank_spread_pct   = bank_spread / mid_rate * 100
      fx_realized_cost  = actual_inr_paid - total_amount  (booking-rate diff)

    Writes to fx_bill_payments and updates fact_invoices FX settlement columns.
    """
    tenant = _get_tenant(body.company_id)
    # Records the INR actually paid for a foreign bill — an INR-books concept.
    if get_jurisdiction(tenant).base_currency != "INR":
        raise HTTPException(status_code=400, detail={
            "error": "NOT_APPLICABLE",
            "detail": "Recording an INR payment applies only to tenants that keep their books in INR",
        })

    try:
        pay_date = date.fromisoformat(body.payment_date) if body.payment_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payment_date format (use YYYY-MM-DD)")

    actual_inr = Decimal(str(body.actual_inr_paid))

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            # Fetch bill from fact_invoices (what the frontend shows)
            cur.execute("""
                SELECT fi.invoice_id, fi.currency_code, fi.original_amount, fi.total_amount,
                       dr.inr_per_unit AS mid_rate, fi.status
                FROM fact_invoices fi
                LEFT JOIN LATERAL (
                    SELECT inr_per_unit
                    FROM dim_fx_rates
                    WHERE currency_code = fi.currency_code
                    ORDER BY rate_date DESC
                    LIMIT 1
                ) dr ON true
                WHERE fi.invoice_id = %s
                  AND fi.tenant_id  = %s
            """, (bill_id, tenant))
            bill = cur.fetchone()

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        fact_invoice_id, currency_code, original_amount, booking_inr, mid_rate, status = bill

        if status in ("PAID", "VOID"):
            raise HTTPException(
                status_code=400,
                detail="Bill is already settled — cannot record an FX payment",
            )

        if not original_amount:
            raise HTTPException(
                status_code=400,
                detail="Bill has no original foreign amount — INR bills do not use FX payments",
            )

        orig = Decimal(str(original_amount))
        effective_rate = actual_inr / orig

        bank_spread = None
        bank_spread_pct = None
        if mid_rate:
            mid = Decimal(str(mid_rate))
            bank_spread = float(effective_rate - mid)
            bank_spread_pct = float(((effective_rate - mid) / mid) * 100)

        fx_realized_cost = None
        if booking_inr:
            fx_realized_cost = float(actual_inr - Decimal(str(booking_inr)))

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO fx_bill_payments (
                    invoice_id, tenant_id, payment_date,
                    foreign_amount, actual_inr, effective_rate,
                    rbi_rate, bank_spread, bank_spread_pct,
                    reference_no, currency_code, bank_name, notes
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                )
                RETURNING payment_id
            """, (
                str(fact_invoice_id), tenant, pay_date,
                float(orig), float(actual_inr), float(effective_rate),
                float(mid_rate) if mid_rate else None, bank_spread, bank_spread_pct,
                body.bank_reference, currency_code, body.bank_name, body.notes,
            ))
            payment_id = str(cur.fetchone()[0])

            # Update fact_invoices FX settlement columns
            cur.execute("""
                UPDATE fact_invoices SET
                    fx_actual_inr     = %s,
                    fx_effective_rate = %s,
                    fx_realized_cost  = %s,
                    updated_at        = now()
                WHERE invoice_id = %s AND tenant_id = %s
            """, (
                float(actual_inr), float(effective_rate),
                fx_realized_cost,
                bill_id, tenant,
            ))

        conn.commit()

    return {
        "payment_id":       payment_id,
        "currency_code":    currency_code,
        "original_amount":  float(orig),
        "actual_inr_paid":  float(actual_inr),
        "effective_rate":   float(effective_rate),
        "bank_spread":      bank_spread,
        "bank_spread_pct":  bank_spread_pct,
        "fx_realized_cost": fx_realized_cost,
        "payment_date":     pay_date.isoformat(),
    }


# ── GET /api/bills/{bill_id}/fx-payments ──────────────────────────────────────

@router.get("/api/bills/{bill_id}/fx-payments")
def list_fx_payments(bill_id: str, company_id: str = Query(default=None)):
    """List all FX payment records for a bill (most recent first)."""
    tenant = _get_tenant(company_id)

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT payment_id, payment_date,
                       foreign_amount, currency_code,
                       actual_inr, effective_rate,
                       rbi_rate, bank_spread, bank_spread_pct,
                       reference_no, bank_name, notes,
                       created_at
                FROM fx_bill_payments
                WHERE invoice_id = %s
                  AND tenant_id  = %s
                ORDER BY payment_date DESC, created_at DESC
            """, (bill_id, tenant))
            rows = cur.fetchall()

    return {
        "payments": [
            {
                "payment_id":      str(r[0]),
                "payment_date":    r[1].isoformat() if r[1] else None,
                "original_amount": float(r[2]) if r[2] is not None else None,
                "currency_code":   (r[3] or "").strip(),
                "actual_inr_paid": float(r[4]) if r[4] is not None else None,
                "effective_rate":  float(r[5]) if r[5] is not None else None,
                "rbi_rate":        float(r[6]) if r[6] is not None else None,
                "bank_spread":     float(r[7]) if r[7] is not None else None,
                "bank_spread_pct": float(r[8]) if r[8] is not None else None,
                "bank_reference":  r[9],
                "bank_name":       r[10],
                "notes":           r[11],
                "created_at":      r[12].isoformat() if r[12] else None,
            }
            for r in rows
        ]
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Alerts, Agent Notes, Settings, Report
# ══════════════════════════════════════════════════════════════════════════════


# ── GET /api/fx/alerts ────────────────────────────────────────────────────────

@router.get("/api/fx/alerts")
def get_fx_alerts(
    company_id: str = Query(default=None),
    status: str = Query(default="ACTIVE"),
):
    """Active FX alerts for tenant. Filter by status: ACTIVE (default), DISMISSED, or ALL."""
    tenant = _get_tenant(company_id)

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            if status.upper() == "ALL":
                cur.execute("""
                    SELECT alert_id, alert_type, currency_code, rate_date,
                           rate_inr, threshold_inr, direction, recommendation,
                           affected_bill_ids, exposure_foreign, exposure_inr,
                           status, created_at, dismissed_at, dismissed_by
                    FROM fx_rate_alerts
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                    LIMIT 50
                """, (tenant,))
            else:
                cur.execute("""
                    SELECT alert_id, alert_type, currency_code, rate_date,
                           rate_inr, threshold_inr, direction, recommendation,
                           affected_bill_ids, exposure_foreign, exposure_inr,
                           status, created_at, dismissed_at, dismissed_by
                    FROM fx_rate_alerts
                    WHERE tenant_id = %s AND status = %s
                    ORDER BY created_at DESC
                    LIMIT 50
                """, (tenant, status.upper()))
            rows = cur.fetchall()

    return {
        "alerts": [
            {
                "alert_id":           str(r[0]),
                "alert_type":         r[1],
                "currency_code":      (r[2] or "").strip(),
                "rate_date":          r[3].isoformat() if r[3] else None,
                "rate_inr":           float(r[4]) if r[4] is not None else None,
                "threshold_inr":      float(r[5]) if r[5] is not None else None,
                "direction":          r[6],
                "recommendation":     r[7],
                "affected_bill_ids":  [str(b) for b in r[8]] if r[8] else [],
                "exposure_foreign":   float(r[9]) if r[9] is not None else None,
                "exposure_inr":       float(r[10]) if r[10] is not None else None,
                "status":             r[11],
                "created_at":         r[12].isoformat() if r[12] else None,
                "dismissed_at":       r[13].isoformat() if r[13] else None,
                "dismissed_by":       r[14],
            }
            for r in rows
        ]
    }


# ── POST /api/fx/alerts/{alert_id}/dismiss ───────────────────────────────────

class DismissAlertRequest(BaseModel):
    dismissed_by: str | None = None
    company_id: str | None = None


@router.post("/api/fx/alerts/{alert_id}/dismiss")
def dismiss_fx_alert(alert_id: str, body: DismissAlertRequest):
    """Mark an alert as DISMISSED."""
    tenant = _get_tenant(body.company_id)

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE fx_rate_alerts
                SET status = 'DISMISSED',
                    dismissed_at = now(),
                    dismissed_by = %s
                WHERE alert_id = %s AND tenant_id = %s AND status = 'ACTIVE'
                RETURNING alert_id
            """, (body.dismissed_by or "user", alert_id, tenant))
            result = cur.fetchone()
        conn.commit()

    if not result:
        raise HTTPException(status_code=404, detail="Alert not found or already dismissed")

    return {"alert_id": str(result[0]), "status": "DISMISSED"}


# ── GET /api/fx/settings ─────────────────────────────────────────────────────

@router.get("/api/fx/settings")
def get_fx_settings(company_id: str = Query(default=None)):
    """Return current FX settings for a tenant."""
    tenant = _get_tenant(company_id)

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT settings_jsonb->'fx' FROM dim_tenants
                WHERE tenant_id = %s
            """, (tenant,))
            row = cur.fetchone()

    if row and row[0]:
        settings = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    else:
        from config import FX_DEFAULT_SETTINGS
        settings = FX_DEFAULT_SETTINGS

    return {"settings": settings}


# ── PUT /api/fx/settings ─────────────────────────────────────────────────────

class FxSettingsRequest(BaseModel):
    company_id: str | None = None
    alerts_enabled: bool | None = None
    reconciliation_tolerance_pct: float | None = None
    stale_alert_days: int | None = None
    thresholds: dict | None = None


@router.put("/api/fx/settings")
def update_fx_settings(body: FxSettingsRequest):
    """Update FX threshold settings for a tenant (partial update)."""
    tenant = _get_tenant(body.company_id)

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT settings_jsonb FROM dim_tenants WHERE tenant_id = %s
            """, (tenant,))
            row = cur.fetchone()

        current_settings = row[0] if row and row[0] else {}
        if isinstance(current_settings, str):
            current_settings = json.loads(current_settings)

        from config import FX_DEFAULT_SETTINGS
        fx = current_settings.get("fx", dict(FX_DEFAULT_SETTINGS))

        if body.alerts_enabled is not None:
            fx["alerts_enabled"] = body.alerts_enabled
        if body.reconciliation_tolerance_pct is not None:
            fx["reconciliation_tolerance_pct"] = body.reconciliation_tolerance_pct
        if body.stale_alert_days is not None:
            fx["stale_alert_days"] = body.stale_alert_days
        if body.thresholds is not None:
            fx.setdefault("thresholds", {})
            for ccy, vals in body.thresholds.items():
                if ccy.upper() in _VALID_CURRENCIES:
                    fx["thresholds"][ccy.upper()] = vals

        current_settings["fx"] = fx

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE dim_tenants
                SET settings_jsonb = %s::jsonb
                WHERE tenant_id = %s
            """, (json.dumps(current_settings), tenant))
        conn.commit()

    return {"settings": fx}


# ── GET /api/fx/report ────────────────────────────────────────────────────────

@router.get("/api/fx/report")
def get_fx_report(
    company_id: str = Query(default=None),
    quarter: str = Query(default=None),
    fy: str = Query(default=None),
):
    """
    FX Cost Analysis report data. Returns per-currency breakdown of
    booked vs actual INR, FX cost, and bank spread analysis.
    Defaults to current Indian FY quarter if not specified.
    """
    tenant = _get_tenant(company_id)

    today = date.today()
    q_start, q_end = _get_quarter_dates(today, quarter, fy)

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            # Per-currency FX cost summary
            cur.execute("""
                SELECT fp.currency_code,
                       SUM(fp.foreign_amount) AS total_foreign,
                       COUNT(*) AS payment_count,
                       SUM(fp.actual_inr) AS total_actual_inr,
                       AVG(fp.effective_rate) AS avg_effective_rate,
                       AVG(fp.bank_spread_pct) AS avg_bank_spread_pct
                FROM fx_bill_payments fp
                WHERE fp.tenant_id = %s
                  AND fp.payment_date BETWEEN %s AND %s
                GROUP BY fp.currency_code
                ORDER BY SUM(fp.actual_inr) DESC
            """, (tenant, q_start, q_end))
            currency_rows = cur.fetchall()

            # Booked INR for same bills (from fact_bills/fact_invoices)
            cur.execute("""
                SELECT fp.currency_code,
                       SUM(fi.total_amount) AS total_booked_inr
                FROM fx_bill_payments fp
                JOIN fact_invoices fi ON fi.invoice_id = fp.invoice_id
                WHERE fp.tenant_id = %s
                  AND fp.payment_date BETWEEN %s AND %s
                GROUP BY fp.currency_code
            """, (tenant, q_start, q_end))
            booked_rows = cur.fetchall()
            booked_map = {
                (r[0] or "").strip(): float(r[1]) if r[1] else 0
                for r in booked_rows
            }

            # Monthly trend
            cur.execute("""
                SELECT DATE_TRUNC('month', fp.payment_date) AS month,
                       SUM(fp.actual_inr) AS actual_inr,
                       COUNT(*) AS payment_count
                FROM fx_bill_payments fp
                WHERE fp.tenant_id = %s
                  AND fp.payment_date BETWEEN %s AND %s
                GROUP BY DATE_TRUNC('month', fp.payment_date)
                ORDER BY month
            """, (tenant, q_start, q_end))
            monthly_rows = cur.fetchall()

    currencies = []
    total_actual = 0
    total_booked = 0

    for r in currency_rows:
        ccy = (r[0] or "").strip()
        actual = float(r[2]) if r[2] is not None else 0  # total_actual_inr is r[3]
        actual_inr = float(r[3]) if r[3] is not None else 0
        booked = booked_map.get(ccy, 0)
        fx_cost = actual_inr - booked

        total_actual += actual_inr
        total_booked += booked

        currencies.append({
            "currency_code":       ccy,
            "total_foreign":       float(r[1]) if r[1] is not None else 0,
            "payment_count":       r[2],
            "booked_inr":          booked,
            "actual_inr":          actual_inr,
            "fx_cost":             round(fx_cost, 2),
            "avg_effective_rate":  round(float(r[4]), 4) if r[4] is not None else None,
            "avg_bank_spread_pct": round(float(r[5]), 2) if r[5] is not None else None,
        })

    monthly = []
    for r in monthly_rows:
        monthly.append({
            "month":         r[0].strftime("%Y-%m") if r[0] else None,
            "actual_inr":    float(r[1]) if r[1] is not None else 0,
            "payment_count": r[2],
        })

    return {
        "quarter_start":  q_start.isoformat(),
        "quarter_end":    q_end.isoformat(),
        "total_booked_inr": round(total_booked, 2),
        "total_actual_inr": round(total_actual, 2),
        "total_fx_cost":    round(total_actual - total_booked, 2),
        "currencies":       currencies,
        "monthly_trend":    monthly,
    }


def _get_quarter_dates(
    today: date, quarter: str | None, fy: str | None
) -> tuple[date, date]:
    """Calculate Indian FY quarter date range. FY runs Apr-Mar."""
    if quarter and fy:
        try:
            parts = fy.split("-")
            start_year = int(parts[0])
            q_num = int(quarter.replace("Q", "").replace("q", ""))
            q_starts = {1: (4, start_year), 2: (7, start_year), 3: (10, start_year), 4: (1, start_year + 1)}
            month, year = q_starts[q_num]
            q_start = date(year, month, 1)
            if q_num == 4:
                q_end = date(year, 3, 31)
            else:
                next_month = month + 3
                q_end = date(year, next_month, 1) - timedelta(days=1)
            return q_start, q_end
        except (ValueError, KeyError, IndexError):
            pass

    # Default: current Indian FY quarter
    m = today.month
    if m in (4, 5, 6):
        return date(today.year, 4, 1), date(today.year, 6, 30)
    elif m in (7, 8, 9):
        return date(today.year, 7, 1), date(today.year, 9, 30)
    elif m in (10, 11, 12):
        return date(today.year, 10, 1), date(today.year, 12, 31)
    else:
        return date(today.year, 1, 1), date(today.year, 3, 31)


# ── GET /api/fx/agent-note ────────────────────────────────────────────────────

@router.get("/api/fx/agent-note")
def get_fx_agent_note(bill_id: str = Query(...)):
    """Return the current AI-generated agent note for a bill.

    Unlike dim_fx_rates, which is market reference data shared by every tenant,
    fx_agent_notes is tenant-owned: the note quotes the bill's own amounts and
    exposure. It is therefore filtered on the authenticated tenant, so a bill_id
    belonging to someone else reads as "no note" rather than theirs.
    """
    tenant = require_tenant()
    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT note_id, severity, note_text, confidence,
                       currency_code, rate_at_generation, created_at
                FROM fx_agent_notes
                WHERE target_type = 'BILL'
                  AND target_id = %s
                  AND tenant_id = %s
                  AND is_current = true
                ORDER BY created_at DESC
                LIMIT 1
            """, (bill_id, tenant))
            row = cur.fetchone()

    if not row:
        return {"note": None}

    return {
        "note": {
            "note_id":            str(row[0]),
            "severity":           row[1],
            "note_text":          row[2],
            "confidence":         float(row[3]) if row[3] is not None else None,
            "currency_code":      (row[4] or "").strip(),
            "rate_at_generation": float(row[5]) if row[5] is not None else None,
            "created_at":         row[6].isoformat() if row[6] else None,
        }
    }


# ── GET /api/fx/agent-briefing ────────────────────────────────────────────────

@router.get("/api/fx/agent-briefing")
def get_fx_agent_briefing(company_id: str = Query(default=None)):
    """Return the most recent AI exposure briefing for a tenant."""
    tenant = _get_tenant(company_id)

    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT note_id, severity, note_text, confidence, created_at
                FROM fx_agent_notes
                WHERE tenant_id = %s
                  AND target_type = 'EXPOSURE'
                  AND is_current = true
                ORDER BY created_at DESC
                LIMIT 1
            """, (tenant,))
            row = cur.fetchone()

    if not row:
        return {"briefing": None}

    return {
        "briefing": {
            "note_id":    str(row[0]),
            "severity":   row[1],
            "note_text":  row[2],
            "confidence": float(row[3]) if row[3] is not None else None,
            "created_at": row[4].isoformat() if row[4] else None,
        }
    }
