"""Audit chain verification, anchoring and proof bundles.

Routes (docs/crypto-rail/08_API_CONTRACT.md §7):

GET  /api/audit/log                   recent audit rows with seq and anchor        (audit_anchor)
GET  /api/audit/verify-chain          re-hash the caller's chain                   (audit_anchor)
POST /api/audit/anchor                anchor every unanchored row now (admin)      (audit_anchor)
GET  /api/audit/anchors               anchors so far                               (audit_anchor)
GET  /api/audit/proof/{log_id}        ProofBundle for one of the caller's rows     (audit_anchor)
GET  /api/public/proofs/{share_token} ProofBundle from a share link                PUBLIC
GET  /api/public/anchors/{anchor_id}  one anchor's root and transaction            PUBLIC

The two public routes take no login by design: an auditor verifies a record
without an Onsumm account. The share token is HMAC-signed over (log_id,
tenant_id), so it opens exactly one record and cannot be forged or edited
into another. Anchors hold only roots and transaction hashes, nothing private.
"""
from __future__ import annotations

import traceback
import uuid

import psycopg2.extras
from fastapi import APIRouter, HTTPException

from ..config import get_conn, put_conn
from ..domain.audit.anchor import AnchorService, ProofError, ProofService, anchor_view, read_share_token
from ..domain.audit.hasher import verify_chain
from ..security.auth import require_role, require_tenant

router = APIRouter(tags=["Crypto rail: audit anchor"])


def _dictcur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _uuid_or_404(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail=f"{what} not found")


@router.get("/api/audit/log")
def audit_log(limit: int = 50):
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                """SELECT log_id::text AS log_id, seq, table_name, entity_id::text AS entity_id,
                          action::text AS action, created_at, anchor_id
                     FROM wf_audit_log WHERE tenant_id = %s
                    ORDER BY seq DESC NULLS LAST, created_at DESC LIMIT %s""",
                (tenant_id, max(1, min(int(limit), 500))),
            )
            rows = cur.fetchall()
        return {"rows": [{**r, "created_at": r["created_at"].isoformat()} for r in rows]}
    finally:
        put_conn(conn)


@router.get("/api/audit/verify-chain")
def audit_verify_chain():
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        result = verify_chain(conn, tenant_id)
        if not result["ok"]:
            from ..domain.crypto.findings import raise_finding
            with _dictcur(conn) as cur:
                raise_finding(cur, tenant_id, "AUDIT_CHAIN_BROKEN", "CRITICAL", "tenant", tenant_id,
                              f"Audit log chain breaks at seq {result['first_broken_seq']}",
                              {"first_broken_seq": result["first_broken_seq"], "checked": result["checked"],
                               "detail": "A stored audit row was edited, deleted or re-ordered."})
            conn.commit()
        return result
    finally:
        put_conn(conn)


@router.post("/api/audit/anchor")
def audit_anchor_now():
    """Anchors every hashed, unanchored row of every tenant in one transaction —
    the same job the daily scheduler runs, started early for a demo."""
    require_tenant()
    require_role("admin")
    conn = get_conn()
    try:
        result = AnchorService().anchor_now(conn)
        if result.anchor is None:
            raise HTTPException(status_code=409, detail="Nothing new to anchor: every audit row is already anchored.")
        return result.anchor
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)


@router.get("/api/audit/anchors")
def audit_anchors(limit: int = 50):
    tenant_id = require_tenant()
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                """SELECT a.* FROM audit_anchors a
                     JOIN audit_anchor_tenant_roots r ON r.anchor_id = a.id AND r.tenant_id = %s
                    ORDER BY a.id DESC LIMIT %s""",
                (tenant_id, max(1, min(int(limit), 200))),
            )
            rows = cur.fetchall()
        service = AnchorService()
        return {"anchors": [anchor_view(service.refresh_status(conn, r)) for r in rows]}
    finally:
        put_conn(conn)


def _bundle(tenant_id: str, log_id: str) -> dict:
    conn = get_conn()
    try:
        return ProofService().bundle(conn, tenant_id, log_id)
    except ProofError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    finally:
        put_conn(conn)


@router.get("/api/audit/proof/{log_id}")
def audit_proof(log_id: str):
    tenant_id = require_tenant()
    return _bundle(tenant_id, _uuid_or_404(log_id, "Audit record"))


@router.get("/api/public/proofs/{share_token}")
def public_proof(share_token: str):
    ids = read_share_token(share_token)
    if ids is None:
        raise HTTPException(status_code=404, detail="This proof link is not valid.")
    log_id, tenant_id = ids
    return _bundle(tenant_id, log_id)


@router.get("/api/public/anchors/{anchor_id}")
def public_anchor(anchor_id: int):
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute("SELECT * FROM audit_anchors WHERE id = %s", (anchor_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Anchor not found")
        return anchor_view(AnchorService().refresh_status(conn, row))
    finally:
        put_conn(conn)
