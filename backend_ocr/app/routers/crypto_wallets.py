"""Vendor payout wallets and contract rates, admin side (08_API_CONTRACT.md §2).

Routes
──────
POST /api/vendors/{supplier_id}/wallet-invites                   signed 72-hour portal link (admin)
GET  /api/vendors/{supplier_id}/wallets                          the vendor's wallets
POST /api/vendors/{supplier_id}/wallets/{wallet_id}/approve      COOLING -> ACTIVE (admin)
POST /api/vendors/{supplier_id}/wallets/{wallet_id}/freeze       any -> FROZEN (admin)
GET  /api/vendors/{supplier_id}/contract-rates                   agreed unit prices
PUT  /api/vendors/{supplier_id}/contract-rates                   replace them (admin)

The vendor proves the wallet themselves through the public portal
(routers/vendor_portal.py); nothing here can register an address, only invite,
approve after cooling, or freeze.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_conn, put_conn
from ..domain.crypto.policy import audit, error, jsonable
from ..domain.crypto.wallets import WalletService
from ..security.auth import current_username, require_role, require_tenant

router = APIRouter(prefix="/api", tags=["Crypto rail: vendor wallets"])


def _dictcur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _assert_vendor(cur, tenant_id: str, supplier_id: str) -> None:
    cur.execute("SELECT 1 FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s", (supplier_id, tenant_id))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Vendor not found")


class InviteBody(BaseModel):
    email: Optional[str] = None


@router.post("/vendors/{supplier_id}/wallet-invites")
def invite_vendor(supplier_id: str, body: InviteBody):
    tenant_id = require_tenant()
    require_role("admin")
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            out = WalletService().invite(cur, tenant_id, supplier_id, body.email, current_username())
            conn.commit()
            return out
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


@router.get("/vendors/{supplier_id}/wallets")
def list_wallets(supplier_id: str):
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            _assert_vendor(cur, tenant_id, supplier_id)
            return {"wallets": WalletService().list(cur, tenant_id, supplier_id)}
    finally:
        put_conn(conn)


@router.post("/vendors/{supplier_id}/wallets/{wallet_id}/approve")
def approve_wallet(supplier_id: str, wallet_id: str):
    tenant_id = require_tenant()
    require_role("admin")
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            out = WalletService().approve(cur, tenant_id, supplier_id, wallet_id, current_username())
            conn.commit()
            return out
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


class FreezeBody(BaseModel):
    reason: str


@router.post("/vendors/{supplier_id}/wallets/{wallet_id}/freeze")
def freeze_wallet(supplier_id: str, wallet_id: str, body: FreezeBody):
    tenant_id = require_tenant()
    require_role("admin")
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            out = WalletService().freeze(cur, tenant_id, supplier_id, wallet_id, body.reason, current_username())
            conn.commit()
            return out
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def _rate_view(row: dict) -> dict:
    out = jsonable(row)
    out["id"] = str(row["id"])
    out["currency_code"] = (row["currency_code"] or "").strip()
    for k in ("tenant_id", "supplier_id", "created_at"):
        out.pop(k, None)
    return out


@router.get("/vendors/{supplier_id}/contract-rates")
def list_contract_rates(supplier_id: str):
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            _assert_vendor(cur, tenant_id, supplier_id)
            cur.execute("SELECT * FROM vendor_contract_rates WHERE tenant_id=%s AND supplier_id=%s ORDER BY item_key",
                        (tenant_id, supplier_id))
            return {"rates": [_rate_view(r) for r in cur.fetchall()]}
    finally:
        put_conn(conn)


class ContractRateIn(BaseModel):
    item_key: str
    unit_price: Decimal
    currency_code: str
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class ContractRatesBody(BaseModel):
    rates: list[ContractRateIn]


@router.put("/vendors/{supplier_id}/contract-rates")
def put_contract_rates(supplier_id: str, body: ContractRatesBody):
    tenant_id = require_tenant()
    require_role("admin")
    for r in body.rates:
        if r.unit_price < 0 or len(r.currency_code.strip()) != 3 or not r.item_key.strip():
            raise error(400, "INVALID_RATE", "Each rate needs an item_key, a non-negative unit_price and a 3-letter currency.")
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            _assert_vendor(cur, tenant_id, supplier_id)
            cur.execute("DELETE FROM vendor_contract_rates WHERE tenant_id=%s AND supplier_id=%s RETURNING item_key, unit_price",
                        (tenant_id, supplier_id))
            old = [jsonable(r) for r in cur.fetchall()]
            for r in body.rates:
                cur.execute(
                    """INSERT INTO vendor_contract_rates (tenant_id, supplier_id, item_key, unit_price, currency_code,
                           valid_from, valid_to) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, supplier_id, r.item_key.strip(), r.unit_price, r.currency_code.strip().upper(),
                     r.valid_from, r.valid_to))
            cur.execute("SELECT * FROM vendor_contract_rates WHERE tenant_id=%s AND supplier_id=%s ORDER BY item_key",
                        (tenant_id, supplier_id))
            rates = [_rate_view(r) for r in cur.fetchall()]
            audit(cur, tenant_id, "dim_suppliers", supplier_id, {"contract_rates": old},
                  {"event": "contract_rates_replaced", "contract_rates": rates}, current_username())
            conn.commit()
            return {"rates": rates}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
