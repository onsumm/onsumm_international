"""WalletService: vendor payout wallets, from invite link to ACTIVE.

Lifecycle (04_LLD.md §4b, §5a):

    invite link -> SIWE proof -> screening -> COOLING -> admin approval -> ACTIVE
                                       +-> HIT -> FROZEN (SCREENING_HIT finding)

A vendor proving a *different* address while it already has a live wallet is
the classic crypto AP fraud (an attacker swaps the payout address). So the old
wallet is FROZEN at once, a WALLET_CHANGED finding is filed, the new wallet
cools, and an admin must approve it; only then is the old one REVOKED.

The vendor portal is public, so it is authenticated by its own link token: an
HMAC over {tenant, supplier, expiry} with a key derived from APPROVAL_SECRET,
valid for VENDOR_PORTAL_TOKEN_TTL_HOURS (72). A forwarded link still only lets
someone *prove* a wallet they control — it never activates one without the
cooling period and an admin.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import urlparse

from web3 import Web3

from ...config import APPROVAL_SECRET, JWT_SECRET, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER
from ...integrations.screening import ScreeningResult, get_screening
from .policy import audit, error, file_finding, jsonable, load_policy
from .settings import get_settings
from .siwe import SiweError, SiweVerifier

logger = logging.getLogger(__name__)


class PortalLinkInvalid(Exception):
    pass


# ── Portal link tokens ────────────────────────────────────────────────────────

def _portal_key() -> bytes:
    secret = APPROVAL_SECRET if APPROVAL_SECRET and APPROVAL_SECRET != "change-me" else JWT_SECRET
    # Domain-separated so a portal token can never be confused with an approval link.
    return hashlib.sha256(f"onsumm:vendor-portal:{secret}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def make_portal_token(tenant_id: str, supplier_id: str, ttl_hours: Optional[int] = None,
                      now: Optional[float] = None) -> tuple[str, datetime]:
    ttl = ttl_hours if ttl_hours is not None else get_settings().vendor_portal_token_ttl_hours
    exp = int((now or time.time()) + ttl * 3600)
    payload = _b64(json.dumps({"t": str(tenant_id), "s": str(supplier_id), "exp": exp},
                              separators=(",", ":")).encode())
    sig = _b64(hmac.new(_portal_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}", datetime.fromtimestamp(exp, tz=timezone.utc)


def read_portal_token(token: str, now: Optional[float] = None) -> dict:
    try:
        payload, sig = (token or "").split(".", 1)
        expected = _b64(hmac.new(_portal_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise PortalLinkInvalid("bad signature")
        claims = json.loads(_unb64(payload))
    except PortalLinkInvalid:
        raise
    except Exception:
        raise PortalLinkInvalid("malformed")
    if int(claims.get("exp", 0)) <= (now or time.time()):
        raise PortalLinkInvalid("expired")
    return {"tenant_id": claims["t"], "supplier_id": claims["s"], "exp": int(claims["exp"])}


# ── Helpers ───────────────────────────────────────────────────────────────────

def wallet_view(row: dict) -> dict:
    out = jsonable(row)
    for k in ("siwe_message", "ownership_sig"):
        out.pop(k, None)
    out["id"] = str(row["id"])
    out["supplier_id"] = str(row["supplier_id"])
    out["explorer"] = f"{get_settings().explorer_url}/address/{row['address']}"
    return out


def record_screening(cur, chain: str, address: str, res: ScreeningResult) -> None:
    cur.execute(
        """INSERT INTO wallet_screenings (chain, address, provider, result, risk_score, categories, raw)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (chain, address, res.provider, res.result, res.risk_score, json.dumps(list(res.categories)),
         json.dumps(res.raw, default=str)),
    )


def screen_wallet(cur, chain: str, address: str) -> ScreeningResult:
    try:
        res = get_screening().screen(chain, address)
    except Exception as e:  # provider bug or outage: fail closed
        res = ScreeningResult("ERROR", "unknown", raw={"error": str(e)})
    record_screening(cur, chain, address, res)
    return res


def latest_screening(cur, address: str) -> Optional[dict]:
    cur.execute(
        "SELECT result, screened_at FROM wallet_screenings WHERE lower(address)=lower(%s) "
        "ORDER BY screened_at DESC LIMIT 1", (address,))
    return cur.fetchone()


def _send_mail(to_email: str, subject: str, body: str) -> bool:
    if not (SMTP_USER and SMTP_PASSWORD and to_email):
        return False
    try:
        msg = MIMEText(body, "plain")
        msg["Subject"], msg["From"], msg["To"] = subject, SMTP_USER, to_email
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())
        return True
    except Exception:
        logger.exception("[crypto] could not send mail to %s", to_email)
        return False


class WalletService:
    def __init__(self):
        s = get_settings()
        self.settings = s
        host = urlparse(s.portal_base_url).netloc or "onsumm.ai"
        self.siwe = SiweVerifier(domain=host, uri=f"{s.portal_base_url}/vendor-portal", chain_id=s.chain_id)

    # ── admin side ──────────────────────────────────────────────────────────
    def _supplier(self, cur, tenant_id: str, supplier_id: str) -> dict:
        cur.execute(
            "SELECT supplier_id, name, email, pan, tax_docs, country_code, tax_residency FROM dim_suppliers "
            "WHERE supplier_id=%s AND tenant_id=%s", (supplier_id, tenant_id))
        row = cur.fetchone()
        if not row:
            raise error(404, "NOT_FOUND", "Vendor not found.")
        return row

    def invite(self, cur, tenant_id: str, supplier_id: str, email: Optional[str], actor: Optional[str]) -> dict:
        supplier = self._supplier(cur, tenant_id, supplier_id)
        token, expires_at = make_portal_token(tenant_id, supplier_id)
        url = f"{self.settings.portal_base_url}/vendor-portal/{token}"
        # Only an address the admin typed for this invite: never silently mail the
        # vendor master's email (databases copied from production hold real vendors).
        to = (email or "").strip()
        emailed = _send_mail(
            to, "Register your payout wallet",
            f"Hello {supplier['name']},\n\nRegister the wallet you want to be paid in USDC at:\n{url}\n\n"
            f"The link is valid until {expires_at:%Y-%m-%d %H:%M} UTC.\n",
        ) if to else False
        audit(cur, tenant_id, "dim_suppliers", supplier_id, None,
              {"event": "wallet_invite_sent", "emailed_to": to if emailed else None,
               "expires_at": expires_at.isoformat()}, actor)
        return {"invite_url": url, "token": token, "expires_at": expires_at.isoformat(),
                "emailed_to": to if emailed else None}

    def list(self, cur, tenant_id: str, supplier_id: str) -> list[dict]:
        cur.execute("SELECT * FROM supplier_wallets WHERE tenant_id=%s AND supplier_id=%s ORDER BY created_at DESC",
                    (tenant_id, supplier_id))
        return [wallet_view(r) for r in cur.fetchall()]

    def _wallet(self, cur, tenant_id: str, supplier_id: str, wallet_id: str) -> dict:
        cur.execute("SELECT * FROM supplier_wallets WHERE id=%s AND tenant_id=%s AND supplier_id=%s",
                    (wallet_id, tenant_id, supplier_id))
        row = cur.fetchone()
        if not row:
            raise error(404, "NOT_FOUND", "Wallet not found.")
        return row

    def approve(self, cur, tenant_id: str, supplier_id: str, wallet_id: str, actor: Optional[str]) -> dict:
        w = self._wallet(cur, tenant_id, supplier_id, wallet_id)
        now = datetime.now(timezone.utc)
        if w["status"] == "ACTIVE":
            return wallet_view(w)
        if w["status"] != "COOLING":
            raise error(409, "WALLET_NOT_ACTIVE", f"A {w['status']} wallet cannot be approved.")
        if w["cooling_until"] and w["cooling_until"] > now:
            raise error(409, "WALLET_COOLING", f"Wallet is cooling until {w['cooling_until']:%H:%M} UTC.")
        if w["screening_status"] != "CLEAR":
            raise error(409, "SCREENING_HIT", "Wallet has not passed screening.")
        cur.execute(
            "UPDATE supplier_wallets SET status='ACTIVE', approved_by=%s, updated_at=now() WHERE id=%s RETURNING *",
            (actor, wallet_id))
        new = cur.fetchone()
        # The wallet this one replaces is retired only now, after cooling and approval.
        cur.execute(
            """UPDATE supplier_wallets SET status='REVOKED', replaced_by=%s, updated_at=now()
               WHERE tenant_id=%s AND supplier_id=%s AND chain=%s AND id<>%s AND status IN ('FROZEN','ACTIVE')
                 AND frozen_reason IS DISTINCT FROM 'SCREENING_HIT'""",
            (wallet_id, tenant_id, supplier_id, w["chain"], wallet_id))
        audit(cur, tenant_id, "supplier_wallets", wallet_id, {"status": w["status"]},
              {"event": "wallet_approved", "status": "ACTIVE"}, actor)
        return wallet_view(new)

    def freeze(self, cur, tenant_id: str, supplier_id: str, wallet_id: str, reason: str, actor: Optional[str]) -> dict:
        w = self._wallet(cur, tenant_id, supplier_id, wallet_id)
        cur.execute(
            "UPDATE supplier_wallets SET status='FROZEN', frozen_reason=%s, updated_at=now() WHERE id=%s RETURNING *",
            (reason or "frozen by admin", wallet_id))
        row = cur.fetchone()
        audit(cur, tenant_id, "supplier_wallets", wallet_id, {"status": w["status"]},
              {"event": "wallet_frozen", "status": "FROZEN", "reason": reason}, actor)
        return wallet_view(row)

    def active_wallet(self, cur, tenant_id: str, supplier_id: str, chain: str) -> Optional[dict]:
        """The wallet a payout would use: ACTIVE first, else the newest live one (for messaging)."""
        cur.execute(
            """SELECT * FROM supplier_wallets WHERE tenant_id=%s AND supplier_id=%s AND chain=%s
                 AND status IN ('ACTIVE','COOLING')
               ORDER BY (status='ACTIVE') DESC, created_at DESC LIMIT 1""",
            (tenant_id, supplier_id, chain))
        return cur.fetchone()

    # ── vendor portal side ──────────────────────────────────────────────────
    def portal_context(self, cur, claims: dict) -> dict:
        cur.execute("SELECT company_name, legal_name FROM dim_tenants WHERE tenant_id=%s", (claims["tenant_id"],))
        tenant = cur.fetchone()
        supplier = self._supplier(cur, claims["tenant_id"], claims["supplier_id"])
        return {
            "supplier_name": supplier["name"],
            "tenant_name": (tenant or {}).get("legal_name") or (tenant or {}).get("company_name"),
            "chain": self.settings.chain, "chain_id": self.settings.chain_id,
            "expires_at": datetime.fromtimestamp(claims["exp"], tz=timezone.utc).isoformat(),
            "wallets": self.list(cur, claims["tenant_id"], claims["supplier_id"]),
            "tax_docs": supplier.get("tax_docs") or {},
        }

    def challenge(self, cur, claims: dict, chain: str, address: str) -> dict:
        if chain != self.settings.chain:
            raise error(400, "CHAIN_NOT_ALLOWED", f"Only {self.settings.chain} is supported.")
        if not Web3.is_address(address or ""):
            raise error(400, "INVALID_ADDRESS", "Not a valid wallet address.")
        ctx = self.portal_context(cur, claims)
        statement = (f"I confirm this wallet receives payments for {ctx['supplier_name']} "
                     f"from {ctx['tenant_name']}.").replace("\n", " ")
        msg = self.siwe.build_message(address, statement)
        cur.execute(
            """INSERT INTO vendor_portal_nonces (nonce, tenant_id, supplier_id, address, message, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (msg.nonce, claims["tenant_id"], claims["supplier_id"], msg.address, msg.text(), msg.expiration_time))
        return {"message": msg.text(), "nonce": msg.nonce, "expires_at": msg.expiration_time.isoformat()}

    def verify(self, cur, claims: dict, message: str, signature: str) -> dict:
        try:
            parsed = self.siwe.verify(message, signature)
        except SiweError as e:
            raise error(400, e.code, str(e))
        cur.execute("SELECT * FROM vendor_portal_nonces WHERE nonce=%s FOR UPDATE", (parsed.nonce,))
        n = cur.fetchone()
        if (not n or str(n["tenant_id"]) != claims["tenant_id"] or str(n["supplier_id"]) != claims["supplier_id"]
                or n["message"] != message or n["address"].lower() != parsed.address.lower()):
            raise error(400, "UNKNOWN_CHALLENGE", "This sign-in message was not issued for this link.")
        if n["used_at"] is not None:
            raise error(400, "REPLAYED", "This sign-in message was already used; request a new one.")
        cur.execute("UPDATE vendor_portal_nonces SET used_at=now() WHERE nonce=%s", (parsed.nonce,))

        tenant_id, supplier_id = claims["tenant_id"], claims["supplier_id"]
        chain = self.settings.chain
        address = Web3.to_checksum_address(parsed.address)
        policy = load_policy(cur, tenant_id)
        res = screen_wallet(cur, chain, address)
        now = datetime.now(timezone.utc)
        actor = f"vendor-portal:{supplier_id}"

        cur.execute(
            """SELECT * FROM supplier_wallets WHERE tenant_id=%s AND supplier_id=%s AND chain=%s
                 AND status IN ('ACTIVE','COOLING','PENDING_PROOF')""",
            (tenant_id, supplier_id, chain))
        live = cur.fetchall()
        same = next((w for w in live if w["address"].lower() == address.lower()), None)
        others = [w for w in live if w is not same]

        if res.result == "HIT":
            status, cooling_until, screening = "FROZEN", None, "HIT"
        elif res.result == "ERROR":
            # Proof is kept, but nothing cools or activates until screening answers.
            status, cooling_until, screening = "PENDING_PROOF", None, "ERROR"
        else:
            status, screening = "COOLING", "CLEAR"
            cooling_until = now + timedelta(minutes=int(policy.get("cooling_minutes") or 0))

        if same and same["status"] == "ACTIVE" and res.result == "CLEAR":
            # Re-proving the wallet already in use: refresh the proof, keep it active.
            cur.execute(
                """UPDATE supplier_wallets SET siwe_message=%s, ownership_sig=%s, verified_at=now(),
                       screening_status='CLEAR', updated_at=now() WHERE id=%s RETURNING *""",
                (message, signature, same["id"]))
            row = cur.fetchone()
        elif same:
            cur.execute(
                """UPDATE supplier_wallets SET siwe_message=%s, ownership_sig=%s, verified_at=now(), status=%s,
                       cooling_until=%s, screening_status=%s,
                       frozen_reason=CASE WHEN %s='FROZEN' THEN 'SCREENING_HIT' ELSE frozen_reason END,
                       updated_at=now() WHERE id=%s RETURNING *""",
                (message, signature, status, cooling_until, screening, status, same["id"]))
            row = cur.fetchone()
        else:
            cur.execute(
                """INSERT INTO supplier_wallets (tenant_id, supplier_id, chain, asset, address, is_primary,
                       siwe_message, ownership_sig, verified_at, cooling_until, screening_status, status, frozen_reason)
                   VALUES (%s,%s,%s,'USDC',%s,true,%s,%s,now(),%s,%s,%s,%s) RETURNING *""",
                (tenant_id, supplier_id, chain, address, message, signature, cooling_until, screening, status,
                 "SCREENING_HIT" if status == "FROZEN" else None))
            row = cur.fetchone()

        wallet_id = str(row["id"])
        if others and res.result != "HIT":
            # A different address while another is live: freeze the old one now.
            for w in others:
                cur.execute(
                    "UPDATE supplier_wallets SET status='FROZEN', frozen_reason='WALLET_CHANGED', updated_at=now() "
                    "WHERE id=%s", (w["id"],))
            file_finding(cur, tenant_id, "WALLET_CHANGED", "high", "supplier", supplier_id,
                         "Vendor registered a new payout wallet",
                         {"new_wallet_id": wallet_id, "new_address": address,
                          "previous": [{"id": str(w["id"]), "address": w["address"]} for w in others]})
            self._notify_previous_contact(cur, tenant_id, supplier_id, address)
        if res.result == "HIT":
            file_finding(cur, tenant_id, "SCREENING_HIT", "critical", "supplier_wallet", wallet_id,
                         "Vendor wallet failed sanctions screening",
                         {"address": address, "provider": res.provider, "categories": list(res.categories)})

        audit(cur, tenant_id, "supplier_wallets", wallet_id, None,
              {"event": "wallet_proved", "address": address, "status": row["status"], "screening": screening}, actor)
        return {
            "wallet_id": wallet_id, "status": row["status"], "screening": screening,
            "cooling_until": row["cooling_until"].isoformat() if row["cooling_until"] else None,
            "needs_admin_approval": row["status"] == "COOLING",
        }

    def _notify_previous_contact(self, cur, tenant_id: str, supplier_id: str, new_address: str) -> None:
        supplier = self._supplier(cur, tenant_id, supplier_id)
        if supplier.get("email"):
            _send_mail(supplier["email"], "Your payout wallet was changed",
                       f"A new payout wallet {new_address} was registered for {supplier['name']}. "
                       f"If this was not you, reply to this email immediately.")

    def save_tax_doc(self, cur, claims: dict, doc_type: str, reference: str) -> dict:
        allowed = {"W-8BEN-E", "TRC", "FORM_10F", "NO_PE"}
        if doc_type not in allowed:
            raise error(400, "INVALID_DOC_TYPE", f"doc_type must be one of {sorted(allowed)}.")
        cur.execute(
            """UPDATE dim_suppliers SET tax_docs = COALESCE(tax_docs,'{}'::jsonb) || jsonb_build_object(%s::text, %s::jsonb)
               WHERE supplier_id=%s AND tenant_id=%s RETURNING tax_docs""",
            (doc_type, json.dumps({"reference": reference, "submitted_at": datetime.now(timezone.utc).isoformat()}),
             claims["supplier_id"], claims["tenant_id"]))
        row = cur.fetchone()
        audit(cur, claims["tenant_id"], "dim_suppliers", claims["supplier_id"], None,
              {"event": "tax_doc_submitted", "doc_type": doc_type}, f"vendor-portal:{claims['supplier_id']}")
        return {"tax_docs": row["tax_docs"] if row else {}}
