"""On-chain ledger: what the chain watcher saw, how it matched, and the vouchers it produced.

Routes (docs/crypto-rail/08_API_CONTRACT.md §5–6), all tenant-scoped from the
token and licensed as `crypto_payouts`:

GET  /api/onchain/status                         watcher health: head, cursor, lag, watched wallets
GET  /api/onchain/transfers                      chain_transfers with match status
POST /api/onchain/rescan                         run one watcher tick + matcher now (admin)
GET  /api/onchain/statements/{date}              daily wallet statement
GET  /api/onchain/postings                       ledger_postings (Tally vouchers)
GET  /api/onchain/postings/{posting_id}/voucher  voucher XML
POST /api/bills/{invoice_id}/crypto-checks       WALLET_MISMATCH + OVERBILLED_VS_CONTRACT for one bill

The chain itself is read by the background loop (domain/crypto/scheduler.py);
these endpoints only read what it stored, except `rescan`, which runs one pass
synchronously so a demo doesn't wait for the next poll.
"""
from __future__ import annotations

import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..config import get_conn, put_conn
from ..domain.crypto.settings import get_settings
from ..integrations.chains.evm import ChainNotConfigured, explorer_tx_url, get_chain
from ..security.auth import require_role, require_tenant

router = APIRouter(prefix="/api", tags=["Crypto rail: on-chain ledger"])

_NOT_CONFIGURED = {"error": "CHAIN_NOT_CONFIGURED",
                   "codes": ["CHAIN_NOT_CONFIGURED"],
                   "messages": ["Set RPC_URL and USDC_ADDRESS to enable the crypto rail."]}


def _dictcur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def transfer_view(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "chain": r["chain"],
        "tx_hash": r["tx_hash"],
        "log_index": r["log_index"],
        "block_number": r["block_number"],
        "block_time": _iso(r.get("block_time")),
        "direction": r["direction"],
        "from_address": r["from_address"],
        "to_address": r["to_address"],
        "amount_units": str(r["amount_units"]),
        "match_type": r.get("match_type"),
        "matched_disbursement_id": str(r["matched_disbursement_id"]) if r.get("matched_disbursement_id") else None,
        "matched_invoice_id": str(r["matched_invoice_id"]) if r.get("matched_invoice_id") else None,
        "status": r["status"],
        "confirmations": r.get("confirmations") or 0,
        "removed": bool(r.get("removed")),
        # Recorded by a demo settlement (CRYPTO_SETTLEMENT=demo), not read from the chain.
        "simulated": bool(r.get("simulated")),
        "explorer": None if r.get("simulated") else explorer_tx_url(r["tx_hash"]),
    }


def _chain_or_none():
    try:
        return get_chain()
    except ChainNotConfigured:
        return None


@router.get("/onchain/status")
def onchain_status():
    tenant_id = require_tenant()
    from ..domain.crypto.watcher import watcher_status
    conn = get_conn()
    try:
        return watcher_status(conn, _chain_or_none(), tenant_id)
    finally:
        put_conn(conn)


@router.get("/onchain/transfers")
def list_transfers(limit: int = 50, status: Optional[str] = None):
    tenant_id = require_tenant()
    limit = max(1, min(int(limit), 500))
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                """SELECT * FROM chain_transfers
                    WHERE tenant_id = %s AND (%s::text IS NULL OR status = %s)
                    ORDER BY block_number DESC, log_index DESC LIMIT %s""",
                (tenant_id, status, status, limit),
            )
            return {"transfers": [transfer_view(r) for r in cur.fetchall()]}
    finally:
        put_conn(conn)


class RescanBody(BaseModel):
    from_block: Optional[int] = None


@router.post("/onchain/rescan")
def rescan(body: Optional[RescanBody] = None):
    require_tenant()
    require_role("admin")
    chain = _chain_or_none()
    if chain is None:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    from ..domain.crypto.recon import OnchainMatcher
    from ..domain.crypto.watcher import ChainWatcher
    conn = get_conn()
    try:
        tick = ChainWatcher(chain).tick(conn, from_block=(body.from_block if body else None))
        OnchainMatcher(chain).run(conn)
        return {"processed": tick["processed"], "cursor_block": tick["cursor_block"]}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=502, detail=f"Chain read failed: {e}")
    finally:
        put_conn(conn)


@router.get("/onchain/statements/{day}")
def wallet_statement(day: str):
    """Opening, movements and closing per wallet for one UTC day.

    `closing_units_ledger` is the running total of every transfer Onsumm has
    recorded for the wallet up to the end of the day. `closing_units_chain` is
    the balance read from the chain, available for today only (a historical
    balance needs an archive node). They agree when no transfer was missed;
    a gap marks the day unreconciled and raises UNRECONCILED_DAY.
    """
    tenant_id = require_tenant()
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="Use a date like 2026-10-31")
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    today = datetime.now(timezone.utc).date() == d
    chain = _chain_or_none() if today else None
    settings = get_settings()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                "SELECT id, role, address FROM treasury_wallets WHERE tenant_id = %s AND chain = %s ORDER BY role",
                (tenant_id, settings.chain),
            )
            wallets = cur.fetchall()
            out = []
            for w in wallets:
                cur.execute(
                    """SELECT
                         COALESCE(sum(CASE WHEN lower(to_address) = lower(%(a)s) AND block_time < %(s)s THEN amount_units END), 0)
                       - COALESCE(sum(CASE WHEN lower(from_address) = lower(%(a)s) AND block_time < %(s)s THEN amount_units END), 0) AS opening,
                         COALESCE(sum(CASE WHEN lower(to_address) = lower(%(a)s) AND block_time >= %(s)s AND block_time < %(e)s THEN amount_units END), 0) AS inflow,
                         COALESCE(sum(CASE WHEN lower(from_address) = lower(%(a)s) AND block_time >= %(s)s AND block_time < %(e)s THEN amount_units END), 0) AS outflow
                       FROM chain_transfers
                      WHERE chain = %(c)s AND NOT removed
                        AND NOT simulated   -- reconcile against the chain only; demo rows are reported apart
                        AND (lower(to_address) = lower(%(a)s) OR lower(from_address) = lower(%(a)s))""",
                    {"a": w["address"], "s": start, "e": end, "c": settings.chain},
                )
                m = cur.fetchone()
                cur.execute(
                    """SELECT COALESCE(sum(amount_units), 0) AS u FROM chain_transfers
                        WHERE chain = %s AND simulated AND NOT removed AND lower(from_address) = lower(%s)
                          AND block_time >= %s AND block_time < %s""",
                    (settings.chain, w["address"], start, end),
                )
                simulated_out = int(cur.fetchone()["u"])
                closing_ledger = int(m["opening"]) + int(m["inflow"]) - int(m["outflow"])
                closing_chain = None
                if chain is not None:
                    try:
                        closing_chain = chain.usdc_balance(w["address"])
                    except Exception:
                        closing_chain = None
                reconciled = None if closing_chain is None else closing_chain == closing_ledger
                if reconciled is False:
                    from ..domain.crypto.findings import raise_finding
                    raise_finding(cur, tenant_id, "UNRECONCILED_DAY", "MEDIUM", "treasury_wallet", str(w["id"]),
                                  f"{w['role'].title()} wallet does not reconcile for {d.isoformat()}",
                                  {"date": d.isoformat(), "address": w["address"],
                                   "ledger_units": str(closing_ledger), "chain_units": str(closing_chain)})
                out.append({
                    "address": w["address"], "role": w["role"],
                    "opening_units": str(int(m["opening"])), "inflow_units": str(int(m["inflow"])),
                    "outflow_units": str(int(m["outflow"])), "closing_units_ledger": str(closing_ledger),
                    # Demo-settlement payouts: recorded, never broadcast, so not part of the balance check.
                    "simulated_outflow_units": str(simulated_out),
                    "closing_units_chain": None if closing_chain is None else str(closing_chain),
                    "reconciled": reconciled,
                })
            cur.execute(
                """SELECT * FROM chain_transfers
                    WHERE tenant_id = %s AND block_time >= %s AND block_time < %s
                    ORDER BY block_number, log_index""",
                (tenant_id, start, end),
            )
            lines = [transfer_view(r) for r in cur.fetchall()]
            conn.commit()
        return {"date": d.isoformat(), "wallets": out, "lines": lines}
    finally:
        put_conn(conn)


@router.get("/onchain/postings")
def list_postings(limit: int = 100):
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                """SELECT id, source_type, source_id, target_system, external_ref, status, error, created_at
                     FROM ledger_postings WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, max(1, min(int(limit), 500))),
            )
            return {"postings": [
                {**{k: (str(v) if k in ("id", "source_id") and v is not None else v) for k, v in r.items()},
                 "created_at": _iso(r["created_at"])}
                for r in cur.fetchall()
            ]}
    finally:
        put_conn(conn)


@router.get("/onchain/postings/{posting_id}/voucher")
def posting_voucher(posting_id: str):
    tenant_id = require_tenant()
    from ..domain.crypto.ledger import voucher_xml
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            xml = voucher_xml(cur, tenant_id, posting_id)
        if xml is None:
            raise HTTPException(status_code=404, detail="Posting not found")
        return Response(content=xml, media_type="application/xml")
    finally:
        put_conn(conn)


@router.post("/bills/{invoice_id}/crypto-checks")
def bill_crypto_checks(invoice_id: str):
    tenant_id = require_tenant()
    from ..domain.crypto.recon import run_bill_checks
    import uuid
    try:
        uuid.UUID(invoice_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Bill not found")
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute("SELECT 1 FROM fact_invoices WHERE invoice_id = %s AND tenant_id = %s", (invoice_id, tenant_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Bill not found")
        findings = run_bill_checks(conn, tenant_id, invoice_id)
        return {"invoice_id": invoice_id, "findings": findings}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)
