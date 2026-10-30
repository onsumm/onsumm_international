"""Foreign-client onboarding for the crypto rail (08_API_CONTRACT.md §1).

Routes
──────
GET    /api/onboarding/profile                    company profile, residency, KYB state
POST   /api/onboarding/kyb                        entity details + KYB documents (admin)
POST   /api/onboarding/kyb/decision               approve / reject KYB (admin)
GET    /api/onboarding/ubos                       beneficial owners
POST   /api/onboarding/ubos                       add a UBO; screened on save (admin)
DELETE /api/onboarding/ubos/{ubo_id}              remove a UBO (admin)
GET    /api/treasury/wallets                      the company's TREASURY / TAX wallets with live balances
POST   /api/treasury/wallets                      connect a Safe (owners + threshold read on-chain) (admin)
POST   /api/treasury/wallets/{wallet_id}/refresh  re-read the Safe; flags SAFE_CONFIG_CHANGED (admin)
GET    /api/tenant/rail-policy                    chains, tokens, limits, cooling, test transfer
PUT    /api/tenant/rail-policy                    update it (admin)

Residency is derived from the entity details, never from a client flag: a
company incorporated or tax-resident in India gets `is_resident_india = true`,
and the crypto rail cannot be enabled for it (00_DECISIONS.md D2).
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from web3 import Web3

from ..config import get_conn, put_conn
from ..domain.crypto.policy import (DEFAULT_POLICY, PolicyEngine, audit, chain_not_configured, error, file_finding,
                                    jsonable, load_policy, refusal)
from ..domain.crypto.settings import get_settings
from ..domain.crypto.treasury_book import treasury_book
from ..integrations.chains.evm import ChainNotConfigured, get_chain
from ..integrations.custody.safe import SafeTxRefused, get_safe_custody
from ..integrations.screening import get_screening
from ..security.auth import current_username, require_role, require_tenant

router = APIRouter(prefix="/api", tags=["Crypto rail: onboarding"])
logger = logging.getLogger(__name__)


def _dictcur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _profile(cur, tenant_id: str) -> dict:
    cur.execute(
        """SELECT tenant_id, company_name, legal_name, country_code, base_currency, jurisdiction_profile,
                  COALESCE(is_resident_india, true) AS is_resident_india, kyb_status, registration_number,
                  tax_residency
             FROM dim_tenants WHERE tenant_id=%s""", (tenant_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    out = jsonable(row)
    out["tenant_id"] = str(row["tenant_id"])
    for k in ("country_code", "base_currency", "tax_residency"):
        out[k] = (out.get(k) or "").strip() or None
    out["crypto_enabled"] = bool(load_policy(cur, tenant_id).get("crypto_enabled"))
    return out


@router.get("/onboarding/profile")
def get_profile():
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            return _profile(cur, tenant_id)
    finally:
        put_conn(conn)


class KybDocument(BaseModel):
    name: str
    note: Optional[str] = None


class KybBody(BaseModel):
    legal_name: str
    country_code: str
    registration_number: Optional[str] = None
    tax_residency: Optional[str] = None
    base_currency: str = "USD"
    documents: list[KybDocument] = []


@router.post("/onboarding/kyb")
def submit_kyb(body: KybBody):
    tenant_id = require_tenant()
    require_role("admin")
    country = body.country_code.strip().upper()
    residency = (body.tax_residency or body.country_code).strip().upper()
    currency = body.base_currency.strip().upper()
    if len(country) != 2 or len(residency) != 2 or len(currency) != 3:
        raise error(400, "INVALID_PROFILE", "Use ISO codes: 2-letter country and residency, 3-letter currency.")
    resident = country == "IN" or residency == "IN"
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            old = _profile(cur, tenant_id)
            cur.execute(
                """UPDATE dim_tenants SET legal_name=%s, country_code=%s, registration_number=%s, tax_residency=%s,
                       base_currency=%s, is_resident_india=%s, jurisdiction_profile=%s, kyb_status='SUBMITTED',
                       settings_jsonb = COALESCE(settings_jsonb,'{}'::jsonb) || jsonb_build_object('kyb_documents', %s::jsonb)
                   WHERE tenant_id=%s""",
                (body.legal_name.strip(), country, body.registration_number, residency, currency, resident,
                 "india" if resident else "international",
                 json.dumps([d.model_dump() for d in body.documents]), tenant_id))
            if resident:
                # Residency is the hard gate: a tenant that becomes India-resident loses the crypto rail.
                cur.execute("UPDATE tenant_rail_policy SET crypto_enabled=false, updated_at=now() WHERE tenant_id=%s",
                            (tenant_id,))
            new = _profile(cur, tenant_id)
            audit(cur, tenant_id, "dim_tenants", tenant_id, old, {"event": "kyb_submitted", **new}, current_username())
            conn.commit()
            return new
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


class KybDecisionBody(BaseModel):
    decision: str
    note: Optional[str] = None


@router.post("/onboarding/kyb/decision")
def kyb_decision(body: KybDecisionBody):
    tenant_id = require_tenant()
    require_role("admin")
    decision = body.decision.strip().upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise error(400, "INVALID_DECISION", "decision must be APPROVED or REJECTED.")
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            old = _profile(cur, tenant_id)
            cur.execute("UPDATE dim_tenants SET kyb_status=%s WHERE tenant_id=%s", (decision, tenant_id))
            audit(cur, tenant_id, "dim_tenants", tenant_id, {"kyb_status": old["kyb_status"]},
                  {"event": "kyb_decision", "kyb_status": decision, "note": body.note}, current_username())
            new = _profile(cur, tenant_id)
            conn.commit()
            return new
    finally:
        put_conn(conn)


def _ubo(row: dict) -> dict:
    out = jsonable(row)
    out["id"] = str(row["id"])
    out.pop("tenant_id", None)
    out["country_code"] = (out.get("country_code") or "").strip() or None
    return out


@router.get("/onboarding/ubos")
def list_ubos():
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute("SELECT * FROM tenant_ubos WHERE tenant_id=%s ORDER BY created_at", (tenant_id,))
            return {"ubos": [_ubo(r) for r in cur.fetchall()]}
    finally:
        put_conn(conn)


class UboBody(BaseModel):
    name: str
    country_code: str
    ownership_pct: float


@router.post("/onboarding/ubos")
def add_ubo(body: UboBody):
    tenant_id = require_tenant()
    require_role("admin")
    if not (0 < body.ownership_pct <= 100):
        raise error(400, "INVALID_UBO", "ownership_pct must be between 0 and 100.")
    try:
        res = get_screening().screen_name(body.name, body.country_code)
    except Exception as e:
        from ..integrations.screening import ScreeningResult
        res = ScreeningResult("ERROR", "unknown", raw={"error": str(e)})
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                """INSERT INTO tenant_ubos (tenant_id, name, country_code, ownership_pct, screening_status, screened_at)
                   VALUES (%s,%s,%s,%s,%s,now()) RETURNING *""",
                (tenant_id, body.name.strip(), body.country_code.strip().upper()[:2], body.ownership_pct, res.result))
            row = cur.fetchone()
            if res.result == "HIT":
                file_finding(cur, tenant_id, "SCREENING_HIT", "critical", "tenant_ubo", str(row["id"]),
                             "Beneficial owner matched a sanctions list", {"name": body.name, "provider": res.provider})
            audit(cur, tenant_id, "tenant_ubos", str(row["id"]), None,
                  {"event": "ubo_added", "name": body.name, "screening": res.result}, current_username())
            conn.commit()
            return _ubo(row)
    finally:
        put_conn(conn)


@router.delete("/onboarding/ubos/{ubo_id}")
def delete_ubo(ubo_id: str):
    tenant_id = require_tenant()
    require_role("admin")
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            cur.execute("DELETE FROM tenant_ubos WHERE id=%s AND tenant_id=%s RETURNING *", (ubo_id, tenant_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="UBO not found")
            audit(cur, tenant_id, "tenant_ubos", ubo_id, _ubo(row), {"event": "ubo_deleted"}, current_username(),
                  action="DELETE")
            conn.commit()
            return {"deleted": True}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


# ── Treasury wallets ──────────────────────────────────────────────────────────

def _treasury_view(row: dict, balance: Optional[int], book: Optional[dict] = None) -> dict:
    s = get_settings()
    return {
        **(book or {}), "settlement_mode": s.settlement_mode,
        "id": str(row["id"]), "role": row["role"], "chain": row["chain"],
        "address": Web3.to_checksum_address(row["address"]), "custody": row["custody"],
        "safe_threshold": row["safe_threshold"],
        "safe_owners": [Web3.to_checksum_address(o) for o in (row["safe_owners"] or [])],
        "status": row["status"], "usdc_balance_units": str(balance) if balance is not None else None,
        "explorer": f"{s.explorer_url}/address/{row['address']}",
    }


@router.get("/treasury/wallets")
def list_treasury_wallets():
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute("SELECT * FROM treasury_wallets WHERE tenant_id=%s ORDER BY role, created_at DESC", (tenant_id,))
            rows = cur.fetchall()
    finally:
        put_conn(conn)
    try:
        chain = get_chain()
    except ChainNotConfigured:
        chain = None
    out = []
    for r in rows:
        balance = None
        if chain is not None:
            try:
                balance = chain.usdc_balance(r["address"])
            except Exception:
                logger.warning("[crypto] balance read failed for %s", r["address"])
        out.append((r, balance))
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            views = []
            for r, balance in out:
                try:
                    cur.execute("SAVEPOINT treasury_book")
                    book = treasury_book(cur, tenant_id, r, balance)
                    cur.execute("RELEASE SAVEPOINT treasury_book")
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT treasury_book")
                    logger.warning("[crypto] treasury book read failed for %s", r["address"], exc_info=True)
                    book = None
                views.append(_treasury_view(r, balance, book))
        conn.rollback()
    finally:
        put_conn(conn)
    return {"wallets": views}


class TreasuryWalletBody(BaseModel):
    role: str
    chain: str
    address: str


def _read_wallet(address: str, role: str) -> tuple[str, Optional[int], Optional[list[str]]]:
    """Custody kind, threshold and owners, read from the chain — never typed in."""
    try:
        chain = get_chain()
    except ChainNotConfigured:
        raise chain_not_configured()
    if not chain.is_contract(address):
        if role == "TREASURY":
            raise error(400, "NOT_A_SAFE", "The treasury wallet must be a Safe multisig.")
        return "EOA", None, None
    try:
        info = get_safe_custody(chain).info(address)
    except SafeTxRefused as e:
        raise error(400, e.code, str(e))
    return "SAFE", info.threshold, list(info.owners)


@router.post("/treasury/wallets")
def add_treasury_wallet(body: TreasuryWalletBody):
    tenant_id = require_tenant()
    require_role("admin")
    role = body.role.strip().upper()
    if role not in ("TREASURY", "TAX"):
        raise error(400, "INVALID_ROLE", "role must be TREASURY or TAX.")
    s = get_settings()
    if body.chain != s.chain:
        raise error(400, "CHAIN_NOT_ALLOWED", f"Only {s.chain} is supported.")
    if not Web3.is_address(body.address or ""):
        raise error(400, "INVALID_ADDRESS", "Not a valid address.")
    address = Web3.to_checksum_address(body.address)
    custody, threshold, owners = _read_wallet(address, role)
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            # One live wallet per role: connecting a new one retires the previous.
            cur.execute("""UPDATE treasury_wallets SET status='INACTIVE', updated_at=now()
                            WHERE tenant_id=%s AND role=%s AND chain=%s AND lower(address)<>lower(%s) AND status='ACTIVE'""",
                        (tenant_id, role, body.chain, address))
            cur.execute(
                """INSERT INTO treasury_wallets (tenant_id, role, chain, address, custody, safe_threshold, safe_owners)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, chain, address) DO UPDATE
                     SET role=EXCLUDED.role, custody=EXCLUDED.custody, safe_threshold=EXCLUDED.safe_threshold,
                         safe_owners=EXCLUDED.safe_owners, status='ACTIVE', updated_at=now()
                   RETURNING *""",
                (tenant_id, role, body.chain, address, custody, threshold, owners))
            row = cur.fetchone()
            audit(cur, tenant_id, "treasury_wallets", str(row["id"]), None,
                  {"event": "treasury_wallet_connected", "role": role, "address": address, "custody": custody,
                   "safe_threshold": threshold, "safe_owners": owners}, current_username())
            conn.commit()
    finally:
        put_conn(conn)
    try:
        balance = get_chain().usdc_balance(address)
    except Exception:
        balance = None
    return _treasury_view(row, balance)


@router.post("/treasury/wallets/{wallet_id}/refresh")
def refresh_treasury_wallet(wallet_id: str):
    tenant_id = require_tenant()
    require_role("admin")
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            cur.execute("SELECT * FROM treasury_wallets WHERE id=%s AND tenant_id=%s FOR UPDATE", (wallet_id, tenant_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Wallet not found")
            custody, threshold, owners = _read_wallet(row["address"], row["role"])
            changed = (sorted(o.lower() for o in (row["safe_owners"] or [])) != sorted(o.lower() for o in (owners or []))
                       or row["safe_threshold"] != threshold)
            if changed:
                file_finding(cur, tenant_id, "SAFE_CONFIG_CHANGED", "high", "treasury_wallet", wallet_id,
                             "Safe owners or threshold changed outside Onsumm",
                             {"before": {"owners": row["safe_owners"], "threshold": row["safe_threshold"]},
                              "after": {"owners": owners, "threshold": threshold}})
            cur.execute("""UPDATE treasury_wallets SET custody=%s, safe_threshold=%s, safe_owners=%s, updated_at=now()
                            WHERE id=%s RETURNING *""", (custody, threshold, owners, wallet_id))
            new = cur.fetchone()
            audit(cur, tenant_id, "treasury_wallets", wallet_id,
                  {"safe_threshold": row["safe_threshold"], "safe_owners": row["safe_owners"]},
                  {"event": "treasury_wallet_refreshed", "changed": changed, "safe_threshold": threshold,
                   "safe_owners": owners}, current_username())
            conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    try:
        balance = get_chain().usdc_balance(new["address"])
    except Exception:
        balance = None
    return _treasury_view(new, balance)


# ── Rail policy ───────────────────────────────────────────────────────────────

def _policy_view(policy: dict) -> dict:
    out = {}
    for k, v in policy.items():
        out[k] = str(v) if isinstance(v, Decimal) else v
    return out


@router.get("/tenant/rail-policy")
def get_rail_policy():
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            return _policy_view(load_policy(cur, tenant_id))
    finally:
        put_conn(conn)


class RailPolicyBody(BaseModel):
    crypto_enabled: Optional[bool] = None
    allowed_chains: Optional[list[str]] = None
    allowed_tokens: Optional[list[str]] = None
    per_tx_limit_usd: Optional[float] = None
    daily_limit_usd: Optional[float] = None
    dual_control_above_usd: Optional[float] = None
    cooling_minutes: Optional[int] = None
    require_test_transfer: Optional[bool] = None


@router.put("/tenant/rail-policy")
def put_rail_policy(body: RailPolicyBody):
    tenant_id = require_tenant()
    require_role("admin")
    changes = body.model_dump(exclude_unset=True)
    if "cooling_minutes" in changes and (changes["cooling_minutes"] is None or changes["cooling_minutes"] < 0):
        raise error(400, "INVALID_POLICY", "cooling_minutes must be zero or more.")
    if changes.get("allowed_tokens") is not None:
        changes["allowed_tokens"] = [t.strip().upper() for t in changes["allowed_tokens"]]
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            old = load_policy(cur, tenant_id)
            if changes.get("crypto_enabled"):
                cur.execute("SELECT COALESCE(is_resident_india, true) AS r, kyb_status FROM dim_tenants WHERE tenant_id=%s",
                            (tenant_id,))
                t = cur.fetchone() or {"r": True}
                gate = PolicyEngine().check_tenant(bool(t["r"]), {"crypto_enabled": True})
                if not gate.allowed:
                    raise refusal(gate)
            merged = {**DEFAULT_POLICY, **old, **changes}
            cur.execute(
                """INSERT INTO tenant_rail_policy (tenant_id, crypto_enabled, allowed_chains, allowed_tokens,
                       per_tx_limit_usd, daily_limit_usd, dual_control_above_usd, cooling_minutes, require_test_transfer)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id) DO UPDATE SET crypto_enabled=EXCLUDED.crypto_enabled,
                       allowed_chains=EXCLUDED.allowed_chains, allowed_tokens=EXCLUDED.allowed_tokens,
                       per_tx_limit_usd=EXCLUDED.per_tx_limit_usd, daily_limit_usd=EXCLUDED.daily_limit_usd,
                       dual_control_above_usd=EXCLUDED.dual_control_above_usd,
                       cooling_minutes=EXCLUDED.cooling_minutes, require_test_transfer=EXCLUDED.require_test_transfer,
                       updated_at=now()""",
                (tenant_id, bool(merged["crypto_enabled"]), merged["allowed_chains"], merged["allowed_tokens"],
                 merged["per_tx_limit_usd"], merged["daily_limit_usd"], merged["dual_control_above_usd"],
                 int(merged["cooling_minutes"]), bool(merged["require_test_transfer"])))
            new = load_policy(cur, tenant_id)
            audit(cur, tenant_id, "tenant_rail_policy", tenant_id, _policy_view(old),
                  {"event": "rail_policy_updated", **_policy_view(new)}, current_username())
            conn.commit()
            return _policy_view(new)
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
