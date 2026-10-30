"""Vendor portal: a vendor proves its payout wallet (08_API_CONTRACT.md §3).

PUBLIC routes — no Onsumm login. Every handler authenticates the link token
from the path itself (HMAC over tenant, supplier and expiry; 72 hours), and the
tenant is taken only from that token. `/api/vendor-portal/` is in
security/auth.py PUBLIC_PREFIXES for this reason.

Routes
──────
GET  /api/vendor-portal/{token}                       who is inviting whom, current wallets
GET  /api/vendor-portal/{token}/wallet/challenge      EIP-4361 message to sign (?chain=&address=)
POST /api/vendor-portal/{token}/wallet/verify         signed message -> screening -> COOLING
POST /api/vendor-portal/{token}/tax-docs              W-8BEN-E / TRC / Form 10F / no-PE reference

Nothing here can make a wallet payable: a proved wallet still cools and needs
an admin's approval.
"""
from __future__ import annotations

import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_conn, put_conn
from ..domain.crypto.policy import error
from ..domain.crypto.wallets import PortalLinkInvalid, WalletService, read_portal_token

router = APIRouter(prefix="/api", tags=["Crypto rail: vendor portal (public)"])


def _dictcur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _claims(token: str) -> dict:
    try:
        return read_portal_token(token)
    except PortalLinkInvalid:
        raise error(401, "PORTAL_LINK_INVALID", "This link is invalid or has expired. Ask the payer for a new one.")


@router.get("/vendor-portal/{token}")
def portal_context(token: str):
    claims = _claims(token)
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            return WalletService().portal_context(cur, claims)
    finally:
        put_conn(conn)


@router.get("/vendor-portal/{token}/wallet/challenge")
def wallet_challenge(token: str, chain: str, address: str):
    claims = _claims(token)
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            out = WalletService().challenge(cur, claims, chain, address)
            conn.commit()
            return out
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


class VerifyBody(BaseModel):
    message: str
    signature: str


@router.post("/vendor-portal/{token}/wallet/verify")
def wallet_verify(token: str, body: VerifyBody):
    claims = _claims(token)
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            out = WalletService().verify(cur, claims, body.message, body.signature)
            conn.commit()
            return out
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


class TaxDocBody(BaseModel):
    doc_type: str
    reference: str


@router.post("/vendor-portal/{token}/tax-docs")
def submit_tax_doc(token: str, body: TaxDocBody):
    claims = _claims(token)
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            out = WalletService().save_tax_doc(cur, claims, body.doc_type, body.reference)
            conn.commit()
            return out
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
