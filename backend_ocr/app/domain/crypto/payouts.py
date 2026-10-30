"""PayoutService: a USDC payout from approved bill to confirmed on-chain transfer.

Lifecycle (00_DECISIONS.md §2.2, 04_LLD.md §5b). `fact_disbursements.status`
keeps the four values the IMPS flow uses, so the Payment Hub, the in-flight
check and reports treat both rails alike; `crypto_disbursement_details.sub_status`
carries the crypto detail:

    PENDING_OTP / AWAITING_RELEASE   prepare(): policy passed, OTP sent
    PENDING / PROPOSED               release_after_otp(): Safe tx built at the next nonce
    PENDING / PARTIALLY_SIGNED       add_signature(): below threshold
    PENDING / READY                  threshold met; relayer submits (or retries)
    PENDING / SUBMITTED              relayer sent execTransaction
    PENDING / CONFIRMING             chain watcher saw it, not deep enough yet
    SUCCESS / CONFIRMED              mark_confirmed(): bill paid, voucher queued
    FAILED  / REJECTED | EXPIRED | REVERTED | REPLACED

What protects the money, in order:
  1. PolicyEngine before anything is signed (residency, policy, wallet,
     cooling, fresh screening, limits, balance, nothing else in flight).
  2. An OTP release, as for IMPS.
  3. The Safe's own threshold of owner signatures — the dual control. Owners
     and threshold are read live from the Safe at every signature.
  4. The Safe nonce: a signed transaction executes at most once, so a relay
     retried after a timeout cannot pay twice.

The functions at the bottom (`expected_transfers`, `mark_confirming`,
`mark_confirmed`, `mark_failed`) are the seam the chain watcher and matcher
use; they never commit, the caller does.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import psycopg2.extras
from fastapi import HTTPException
from web3 import Web3

from ...config import DISBURSEMENT_DEMO_OTP, SMTP_ADMIN_EMAIL, get_conn, put_conn
from ...integrations.chains.evm import ChainNotConfigured, explorer_tx_url, get_chain
from ...integrations.custody.base import SafeInfo, Transfer
from ...integrations.custody.relayer import RelayerError
from ...integrations.custody.safe import SafeTxRefused, get_safe_custody, typed_data
from ..payments import demo as demo_payments
from .fx import FxRateUnavailable, FxService, UNITS_PER_TOKEN
from .policy import (PayoutContext, PolicyEngine, PolicyResult, SCREENING_MAX_AGE, audit, chain_not_configured,
                     error, file_finding, jsonable, load_policy, refusal)
from .settings import get_settings
from .treasury_book import available_units
from .wallets import WalletService, _send_mail, latest_screening, screen_wallet
from .withholding import WithholdingEngine, WithholdingInput, WithholdingResult

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 120
MAX_OTP_ATTEMPTS = 5
TERMINAL = ("SUCCESS", "FAILED")
SIGNABLE = ("PROPOSED", "PARTIALLY_SIGNED", "READY")
PRE_SUBMIT = ("AWAITING_RELEASE",) + SIGNABLE
NONCE_HOLDING = SIGNABLE + ("SUBMITTED",)


def _dictcur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _hash_otp(otp: str) -> str:
    # Same scheme as routers/disbursement.py, copied rather than imported: domain
    # code does not import routers.
    return hashlib.sha256(otp.encode()).hexdigest()


def _check_otp(supplied: str, stored_hash: Optional[str]) -> tuple[bool, bool]:
    supplied = (supplied or "").strip()
    if DISBURSEMENT_DEMO_OTP and hmac.compare_digest(supplied, DISBURSEMENT_DEMO_OTP):
        return True, True
    if not stored_hash:
        return False, False
    return hmac.compare_digest(_hash_otp(supplied), stored_hash), False


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    return (name[:2] + "***@" + domain) if len(name) > 2 else ("***@" + domain)


def _units(v) -> int:
    return int(Decimal(v or 0))


def _cs(addr: Optional[str]) -> Optional[str]:
    return Web3.to_checksum_address(addr) if addr else None


def _supplier_is_resident(supplier: dict) -> bool:
    residency = (supplier.get("tax_residency") or supplier.get("country_code") or "").strip().upper()
    if residency:
        return residency == "IN"
    return bool(supplier.get("gstn") or supplier.get("pan"))


class PayoutService:
    def __init__(self, chain=None, custody=None):
        self._chain = chain
        self._custody = custody
        self.policy = PolicyEngine()
        self.fx = FxService()
        self.withholding = WithholdingEngine()
        self.settings = get_settings()

    # ── dependencies ────────────────────────────────────────────────────────
    def chain(self):
        if self._chain is None:
            try:
                self._chain = get_chain()
            except ChainNotConfigured:
                raise chain_not_configured()
        return self._chain

    def custody(self):
        if self._custody is None:
            self._custody = get_safe_custody(self.chain())
        return self._custody

    # ── loaders ─────────────────────────────────────────────────────────────
    def _bill(self, cur, tenant_id: str, invoice_id: str) -> dict:
        cur.execute(
            """SELECT invoice_id, invoice_number, supplier_id, outstanding_amount, total_amount, amount_paid,
                      currency_code, stg_bill_id
                 FROM fact_invoices WHERE invoice_id=%s AND tenant_id=%s""",
            (invoice_id, tenant_id))
        bill = cur.fetchone()
        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")
        if not bill["supplier_id"]:
            raise HTTPException(status_code=400, detail="Bill has no linked vendor")
        return bill

    def _tenant(self, cur, tenant_id: str) -> dict:
        cur.execute(
            """SELECT tenant_id, company_name, legal_name, primary_contact_email,
                      COALESCE(is_resident_india, true) AS is_resident_india,
                      COALESCE(base_currency, 'INR') AS base_currency, COALESCE(settings_jsonb, '{}'::jsonb) AS settings
                 FROM dim_tenants WHERE tenant_id=%s""", (tenant_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return row

    def _supplier(self, cur, tenant_id: str, supplier_id: str) -> dict:
        cur.execute(
            """SELECT supplier_id, name, legal_name, email, pan, gstn, COALESCE(tax_docs,'{}'::jsonb) AS tax_docs,
                      country_code, tax_residency, vendor_category_id, category
                 FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s""", (supplier_id, tenant_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return row

    def _treasury(self, cur, tenant_id: str, role: str) -> Optional[dict]:
        cur.execute(
            """SELECT * FROM treasury_wallets WHERE tenant_id=%s AND role=%s AND chain=%s AND status='ACTIVE'
               ORDER BY updated_at DESC LIMIT 1""", (tenant_id, role, self.settings.chain))
        return cur.fetchone()

    def _bill_tds(self, cur, stg_bill_id) -> tuple[Optional[Decimal], Optional[Decimal], Optional[str]]:
        if not stg_bill_id:
            return None, None, None
        try:
            cur.execute("SAVEPOINT crypto_tds")
            cur.execute("SELECT tds_amount, tds_rate, tds_section_code FROM fact_bills WHERE stg_bill_id=%s LIMIT 1",
                        (stg_bill_id,))
            r = cur.fetchone()
            cur.execute("RELEASE SAVEPOINT crypto_tds")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT crypto_tds")
            return None, None, None
        if not r:
            return None, None, None
        return r["tds_amount"], r["tds_rate"], r["tds_section_code"]

    def _withholding_input(self, cur, tenant: dict, supplier: dict, bill: dict, amount: Decimal,
                           rail: str) -> WithholdingInput:
        tds_amount, tds_rate, tds_section = self._bill_tds(cur, bill.get("stg_bill_id"))
        currency = (bill["currency_code"] or "USD").strip().upper()
        try:
            amount_inr = amount * FxService.usd_rate("INR") / FxService.usd_rate(currency)
        except FxRateUnavailable:
            amount_inr = None
        return WithholdingInput(
            rail=rail, tenant_settings=tenant["settings"] or {},
            tenant_is_resident_india=bool(tenant["is_resident_india"]),
            supplier_category=str(supplier["vendor_category_id"] or supplier.get("category") or "") or None,
            supplier_pan=supplier.get("pan"), supplier_tax_docs=supplier.get("tax_docs") or {},
            supplier_is_resident_india=_supplier_is_resident(supplier),
            bill_amount=amount, bill_currency=currency,
            bill_tds_amount=tds_amount, bill_tds_rate=tds_rate, bill_tds_section=tds_section,
            amount_inr=amount_inr,
        )

    def _inflight(self, cur, tenant_id: str, invoice_id: str) -> tuple[Decimal, tuple]:
        cur.execute(
            """SELECT COALESCE(SUM(amount), 0) AS inflight,
                      COALESCE(array_agg(DISTINCT COALESCE(rail, 'IMPS')) FILTER (WHERE disbursement_id IS NOT NULL), '{}') AS rails
                 FROM fact_disbursements
                WHERE tenant_id=%s AND invoice_id=%s AND status <> ALL(%s)""",
            (tenant_id, invoice_id, list(TERMINAL)))
        r = cur.fetchone()
        return Decimal(r["inflight"] or 0), tuple(r["rails"] or ())

    def _daily_total_usd(self, cur, tenant_id: str) -> Decimal:
        cur.execute(
            """SELECT COALESCE(SUM(c.gross_units), 0) AS units
                 FROM crypto_disbursement_details c
                 JOIN fact_disbursements f ON f.disbursement_id = c.disbursement_id
                WHERE c.tenant_id=%s AND f.status <> 'FAILED'
                  AND c.created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
            (tenant_id,))
        return Decimal(cur.fetchone()["units"] or 0) / UNITS_PER_TOKEN

    def _fresh_screening(self, cur, tenant_id: str, wallet: Optional[dict]) -> Optional[str]:
        """CLEAR / HIT / ERROR from a screening no older than 24 h, re-screening if needed."""
        if not wallet:
            return None
        last = latest_screening(cur, wallet["address"])
        now = datetime.now(timezone.utc)
        if last and last["result"] in ("CLEAR", "HIT") and now - last["screened_at"] < SCREENING_MAX_AGE:
            result = last["result"]
        else:
            result = screen_wallet(cur, wallet["chain"], wallet["address"]).result
        if result == "HIT" and wallet["status"] != "FROZEN":
            cur.execute("UPDATE supplier_wallets SET status='FROZEN', frozen_reason='SCREENING_HIT', "
                        "screening_status='HIT', updated_at=now() WHERE id=%s", (wallet["id"],))
            file_finding(cur, tenant_id, "SCREENING_HIT", "critical", "supplier_wallet", str(wallet["id"]),
                         "Vendor wallet failed screening at payout", {"address": wallet["address"]})
        return result

    def _sync_safe(self, cur, tenant_id: str, treasury: dict, info: SafeInfo, actor: Optional[str]) -> None:
        """Keep our copy of owners/threshold in step with the Safe; a change made
        outside Onsumm is legitimate but must be visible."""
        stored = sorted((o.lower() for o in (treasury.get("safe_owners") or [])))
        live = sorted(o.lower() for o in info.owners)
        if stored != live or treasury.get("safe_threshold") != info.threshold:
            cur.execute("UPDATE treasury_wallets SET safe_owners=%s, safe_threshold=%s, updated_at=now() WHERE id=%s",
                        (list(info.owners), info.threshold, treasury["id"]))
            file_finding(cur, tenant_id, "SAFE_CONFIG_CHANGED", "high", "treasury_wallet", str(treasury["id"]),
                         "Safe owners or threshold changed outside Onsumm",
                         {"before": {"owners": treasury.get("safe_owners"), "threshold": treasury.get("safe_threshold")},
                          "after": {"owners": list(info.owners), "threshold": info.threshold}})
            audit(cur, tenant_id, "treasury_wallets", str(treasury["id"]),
                  {"safe_owners": treasury.get("safe_owners"), "safe_threshold": treasury.get("safe_threshold")},
                  {"event": "safe_config_changed", "safe_owners": list(info.owners), "safe_threshold": info.threshold},
                  actor or "system:crypto")

    # ── preview (no writes) ─────────────────────────────────────────────────
    def preview_withholding(self, tenant_id: str, invoice_id: str, rail: str, amount: Optional[Decimal]) -> dict:
        conn = get_conn()
        try:
            with _dictcur(conn) as cur:
                bill = self._bill(cur, tenant_id, invoice_id)
                tenant = self._tenant(cur, tenant_id)
                supplier = self._supplier(cur, tenant_id, str(bill["supplier_id"]))
                amount = Decimal(str(amount)) if amount else Decimal(bill["outstanding_amount"] or 0)
                rail = (rail or "USDC").upper()
                policy_res = self.policy.check_tenant(bool(tenant["is_resident_india"]), load_policy(cur, tenant_id)) \
                    if rail == "USDC" else PolicyResult()
                inp = self._withholding_input(cur, tenant, supplier, bill, amount, rail)
                try:
                    snap = self.fx.quote(inp.bill_currency, tenant["base_currency"])
                    gross = snap.bill_to_units(amount)
                except FxRateUnavailable as e:
                    raise error(400, "FX_RATE_UNAVAILABLE", str(e))
                res = self.withholding.compute(inp, snap, gross)
                out = {**res.as_dict(), "policy": policy_res.as_dict(), "gross_units": str(gross),
                       "amount_usd": f"{snap.bill_to_usd(amount):.2f}", "token_usd": str(snap.token_usd)}
                if tenant["is_resident_india"]:
                    # The guardrail moment: crypto is blocked, so show what the bank rail withholds.
                    bank_inp = self._withholding_input(cur, tenant, supplier, bill, amount, "IMPS")
                    bank = self.withholding.compute(bank_inp, None, 0)
                    out["alternative"] = {
                        "rail": "IMPS", "rule_pack": bank.rule_pack, "rule_code": bank.rule_code,
                        "rate": str(bank.rate), "amount": f"{bank.tax_amount:.2f}", "currency": bank.base_currency,
                        "note": bank.note,
                    }
                conn.rollback()
                return out
        finally:
            put_conn(conn)

    # ── prepare ─────────────────────────────────────────────────────────────
    def prepare(self, tenant_id: str, invoice_id: str, amount, actor: Optional[str]) -> dict:
        conn = get_conn()
        conn.autocommit = False
        try:
            with _dictcur(conn) as cur:
                bill = self._bill(cur, tenant_id, invoice_id)
                tenant = self._tenant(cur, tenant_id)
                policy = load_policy(cur, tenant_id)
                gate = self.policy.check_tenant(bool(tenant["is_resident_india"]), policy)
                if not gate.allowed:
                    raise refusal(gate)

                try:
                    amount = Decimal(str(amount))
                except Exception:
                    raise error(400, "INVALID_AMOUNT", "Amount must be a number.")
                outstanding = Decimal(bill["outstanding_amount"] or 0)
                currency = (bill["currency_code"] or "USD").strip().upper()
                if amount <= 0 or amount > outstanding + Decimal("0.01"):
                    raise error(400, "INVALID_AMOUNT",
                                f"Amount must be between 0 and the outstanding {currency} {outstanding:,.2f}.")

                supplier_id = str(bill["supplier_id"])
                supplier = self._supplier(cur, tenant_id, supplier_id)
                chain = self.chain()
                custody = self.custody()
                wallet = WalletService().active_wallet(cur, tenant_id, supplier_id, self.settings.chain)
                treasury = self._treasury(cur, tenant_id, "TREASURY")
                if not treasury:
                    raise error(400, "NO_TREASURY_WALLET", "Connect the company's Safe as its treasury wallet first.")
                tax_wallet = self._treasury(cur, tenant_id, "TAX")

                try:
                    snap = self.fx.snapshot(cur, currency, tenant["base_currency"])
                except FxRateUnavailable as e:
                    raise error(400, "FX_RATE_UNAVAILABLE", str(e))
                gross_units = snap.bill_to_units(amount)
                wh: WithholdingResult = self.withholding.compute(
                    self._withholding_input(cur, tenant, supplier, bill, amount, "USDC"), snap, gross_units)
                if wh.amount_units > 0 and not tax_wallet:
                    raise error(400, "NO_TAX_WALLET", "Withholding applies; register the company's TAX wallet first.")
                if wh.net_units <= 0:
                    raise error(400, "INVALID_AMOUNT", "Nothing left to pay the vendor after withholding.")

                try:
                    balance = custody.balance(treasury["address"])
                except Exception:
                    logger.exception("[crypto] treasury balance read failed")
                    balance = None
                # Net of demo-settled payouts and payouts still waiting on the Safe.
                balance = available_units(cur, tenant_id, treasury, balance)
                inflight, rails = self._inflight(cur, tenant_id, invoice_id)
                ctx = PayoutContext(
                    tenant_id=tenant_id, is_resident_india=bool(tenant["is_resident_india"]), policy=policy,
                    chain=self.settings.chain, token="USDC", wallet=wallet,
                    screening_result=self._fresh_screening(cur, tenant_id, wallet),
                    amount_usd=snap.bill_to_usd(amount), daily_total_usd=self._daily_total_usd(cur, tenant_id),
                    gross_units=gross_units, treasury_balance_units=balance, outstanding=outstanding,
                    amount=amount, inflight_amount=inflight, inflight_rails=rails,
                )
                verdict = self.policy.check_payout(ctx)
                if not verdict.allowed:
                    if "ALREADY_IN_FLIGHT" in verdict.codes and any(r != "USDC" for r in rails):
                        file_finding(cur, tenant_id, "DUPLICATE_ACROSS_RAILS", "high", "invoice", invoice_id,
                                     "Bill already being paid on another rail",
                                     {"rails_in_flight": list(rails), "attempted": "USDC", "amount": str(amount)})
                    conn.commit()   # keep the screening record and any finding
                    raise refusal(verdict)

                try:
                    info = custody.info(treasury["address"])
                except SafeTxRefused as e:
                    raise error(400, e.code, str(e))
                self._sync_safe(cur, tenant_id, treasury, info, actor)

                otp = "".join(random.choices(string.digits, k=6))
                otp_expires_at = time.time() + OTP_TTL_SECONDS
                tax_fiat = wh.tax_amount if wh.base_currency == currency else Decimal(0)
                cur.execute(
                    """INSERT INTO fact_disbursements
                           (tenant_id, invoice_id, supplier_id, amount, beneficiary_name, beneficiary_account,
                            beneficiary_ifsc, remitter_account_number, bank_provider, tran_ref_no, status,
                            otp_code_hash, otp_expires_at, initiated_by, rail, currency_code,
                            withholding_amount, net_amount)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'USDC',%s,'PENDING_OTP',%s,%s,%s,'USDC',%s,%s,%s)
                       RETURNING disbursement_id""",
                    (tenant_id, invoice_id, supplier_id, amount, supplier["name"], wallet["address"],
                     self.settings.chain, treasury["address"], "USDC-" + secrets.token_hex(6).upper(),
                     _hash_otp(otp), otp_expires_at, actor, currency, tax_fiat, amount - tax_fiat))
                disbursement_id = str(cur.fetchone()["disbursement_id"])
                cur.execute(
                    """INSERT INTO crypto_disbursement_details
                           (disbursement_id, tenant_id, chain, token_contract, treasury_wallet_id, supplier_wallet_id,
                            tax_wallet_id, gross_units, amount_units, withholding_units, fx_snapshot_id,
                            idempotency_key, sub_status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'AWAITING_RELEASE')""",
                    (disbursement_id, tenant_id, self.settings.chain, chain.usdc_address, treasury["id"], wallet["id"],
                     tax_wallet["id"] if (tax_wallet and wh.amount_units > 0) else None, gross_units, wh.net_units,
                     wh.amount_units, snap.id, f"{disbursement_id}:1"))
                cur.execute(
                    """INSERT INTO crypto_withholdings
                           (tenant_id, disbursement_id, rule_pack, rule_code, rate, base_amount, base_currency,
                            amount_units, requires_signoff, note)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, disbursement_id, wh.rule_pack, wh.rule_code, wh.rate, wh.base_amount,
                     wh.base_currency, wh.amount_units, wh.requires_signoff, wh.note))
                if wh.requires_signoff:
                    file_finding(cur, tenant_id, "WITHHOLDING_SIGNOFF_REQUIRED", "medium", "disbursement",
                                 disbursement_id, "Withholding rule needs adviser sign-off",
                                 {"rule_pack": wh.rule_pack, "rule_code": wh.rule_code, "rate": str(wh.rate)})
                audit(cur, tenant_id, "fact_disbursements", disbursement_id, None,
                      {"event": "initiated", "rail": "USDC", "amount": str(amount), "currency": currency,
                       "invoice_id": invoice_id, "gross_units": str(gross_units), "net_units": str(wh.net_units),
                       "withholding_units": str(wh.amount_units), "rule_pack": wh.rule_pack,
                       "to": wallet["address"], "safe": treasury["address"], "fx_snapshot_id": snap.id}, actor)
                conn.commit()

            response = {
                "disbursement_id": disbursement_id, "status": "PENDING_OTP", "sub_status": "AWAITING_RELEASE",
                "rail": "USDC",
                "bill": {"invoice_id": str(bill["invoice_id"]), "invoice_number": bill["invoice_number"],
                         "billed": f"{Decimal(bill['total_amount'] or 0):.2f}", "approved": f"{amount:.2f}",
                         "currency": currency},
                "quote": {k: v for k, v in snap.as_dict().items() if k != "source"},
                "withholding": {"rule_pack": wh.rule_pack, "rule_code": wh.rule_code, "rate": str(wh.rate),
                                "amount_units": str(wh.amount_units), "requires_signoff": wh.requires_signoff},
                "gross_units": str(gross_units), "net_units": str(wh.net_units),
                "to": {"supplier": supplier["name"], "address": _cs(wallet["address"]), "chain": self.settings.chain},
                "from": {"safe": _cs(treasury["address"]), "threshold": info.threshold, "owners": len(info.owners)},
                "tax_wallet": _cs(tax_wallet["address"]) if (tax_wallet and wh.amount_units > 0) else None,
                "estimated_gas_fee": self._estimate_gas(wh.amount_units > 0),
                "policy": verdict.as_dict(),
                "otp_expires_at": otp_expires_at, "otp_ttl_seconds": OTP_TTL_SECONDS,
            }
            if demo_payments.is_demo(tenant_id):
                return {**response, "otp_sent_to": "", "demo_otp": otp, "demo_payment": True}
            to = tenant.get("primary_contact_email") or SMTP_ADMIN_EMAIL
            sent = _send_mail(
                to, "Vendor payment OTP (USDC)",
                f"A USDC payout of {currency} {amount:,.2f} to {supplier['name']} is waiting for release.\n\n"
                f"OTP: {otp}\n\nValid for 2 minutes. Do not share it.") if to else False
            return {**response, "otp_sent_to": _mask_email(to) if sent else "",
                    "demo_otp": DISBURSEMENT_DEMO_OTP or None}
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            logger.exception("[crypto] prepare failed")
            raise
        finally:
            put_conn(conn)

    def _estimate_gas(self, multisend: bool) -> str:
        try:
            gas = 160_000 if multisend else 110_000
            wei = int(self.chain().w3.eth.gas_price) * gas
            return f"{Decimal(wei) / Decimal(10**18):.8f} ETH"
        except Exception:
            return "unknown"

    # ── release (OTP) -> Safe proposal ──────────────────────────────────────
    def _load(self, cur, tenant_id: str, disbursement_id: str, lock: bool = False) -> dict:
        cur.execute(
            f"""SELECT f.*, c.*, f.status AS status, c.disbursement_id AS crypto_id
                  FROM fact_disbursements f
                  JOIN crypto_disbursement_details c ON c.disbursement_id = f.disbursement_id
                 WHERE f.disbursement_id=%s AND f.tenant_id=%s {'FOR UPDATE OF f, c' if lock else ''}""",
            (disbursement_id, tenant_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="USDC disbursement not found")
        return row

    def _tx(self, cur, row: dict) -> tuple[dict, list[Transfer], str]:
        transfers = [Transfer(t["to_address"], t["amount_units"]) for t in expected_transfers(cur, str(row["disbursement_id"]))]
        safe = row["remitter_account_number"]
        tx = self.custody().build_payout(safe, transfers, int(row["safe_nonce"]))
        return tx, transfers, safe

    def release_after_otp(self, tenant_id: str, disbursement_id: str, otp: str, actor: Optional[str]) -> dict:
        conn = get_conn()
        conn.autocommit = False
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id, lock=True)
                if row["status"] != "PENDING_OTP":
                    raise error(409, "NOT_AWAITING_OTP", f"Payout is {row['status']} / {row['sub_status']}.")
                ok, via_demo = _check_otp(otp, row["otp_code_hash"])
                if not via_demo and row["otp_expires_at"] and row["otp_expires_at"] < time.time():
                    _fail(cur, tenant_id, row, "EXPIRED", "OTP expired — no code entered within 2 minutes", actor)
                    conn.commit()
                    raise error(400, "OTP_EXPIRED", "OTP expired. Start the payment again.")
                if not ok:
                    attempts = int(row["otp_attempts"] or 0) + 1
                    cur.execute("UPDATE fact_disbursements SET otp_attempts=%s, updated_at=now() WHERE disbursement_id=%s",
                                (attempts, disbursement_id))
                    if attempts >= MAX_OTP_ATTEMPTS:
                        _fail(cur, tenant_id, row, "REJECTED", "Too many wrong OTP attempts", actor)
                    conn.commit()
                    raise error(400, "OTP_INVALID", f"Incorrect OTP ({MAX_OTP_ATTEMPTS - attempts} attempts left).")

                custody = self.custody()
                safe = row["remitter_account_number"]
                info = custody.info(safe)
                cur.execute(
                    f"""SELECT MAX(c.safe_nonce) AS n FROM crypto_disbursement_details c
                         WHERE c.treasury_wallet_id=%s AND c.disbursement_id<>%s
                           AND c.sub_status = ANY(%s)""",
                    (row["treasury_wallet_id"], disbursement_id, list(NONCE_HOLDING)))
                held = cur.fetchone()["n"]
                nonce = max(info.nonce, int(held) + 1 if held is not None else info.nonce)
                if self.settings.demo_settlement():
                    # A demo settlement is never broadcast, so the Safe's nonce never
                    # moves: every payout must be signed at the Safe's current nonce.
                    nonce = info.nonce
                transfers = [Transfer(t["to_address"], t["amount_units"]) for t in expected_transfers(cur, disbursement_id)]
                tx = custody.build_payout(safe, transfers, nonce)
                tx_hash = "0x" + custody.tx_hash(safe, tx).hex()
                cur.execute(
                    """UPDATE crypto_disbursement_details SET safe_tx_hash=%s, safe_nonce=%s, sub_status='PROPOSED',
                           updated_at=now() WHERE disbursement_id=%s""", (tx_hash, nonce, disbursement_id))
                cur.execute(
                    """UPDATE fact_disbursements SET status='PENDING', otp_verified_at=now(),
                           status_message='Waiting for Safe owner signatures', updated_at=now(), updated_by=%s
                       WHERE disbursement_id=%s""", (actor, disbursement_id))
                audit(cur, tenant_id, "fact_disbursements", disbursement_id, {"status": "PENDING_OTP"},
                      {"event": "released", "sub_status": "PROPOSED", "safe_tx_hash": tx_hash, "nonce": nonce,
                       "multisend": int(tx["operation"]) == 1, "demo_otp": via_demo}, actor)
                conn.commit()
                return {"disbursement_id": disbursement_id, "status": "PENDING", "sub_status": "PROPOSED",
                        "safe_tx_hash": tx_hash, "nonce": nonce}
        except HTTPException:
            conn.rollback()
            raise
        except SafeTxRefused as e:
            conn.rollback()
            raise error(400, e.code, str(e))
        finally:
            put_conn(conn)

    # ── signing ─────────────────────────────────────────────────────────────
    def _signatures(self, cur, disbursement_id: str) -> list[dict]:
        cur.execute("""SELECT signer_address, signature, user_id, signed_at FROM crypto_payout_signatures
                        WHERE disbursement_id=%s ORDER BY signed_at""", (disbursement_id,))
        return cur.fetchall()

    def safe_tx_view(self, tenant_id: str, disbursement_id: str) -> dict:
        conn = get_conn()
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id)
                if row["safe_nonce"] is None:
                    raise error(409, "NOT_RELEASED", "Release the payout with its OTP first.")
                tx, transfers, safe = self._tx(cur, row)
                onchain_nonce = None
                try:
                    info = self.custody().info(safe)
                    threshold, onchain_nonce, owners = info.threshold, info.nonce, list(info.owners)
                except Exception:
                    cur.execute("SELECT safe_threshold, safe_owners FROM treasury_wallets WHERE id=%s",
                                (row["treasury_wallet_id"],))
                    tw = cur.fetchone() or {}
                    threshold, owners = tw.get("safe_threshold"), list(tw.get("safe_owners") or [])
                sigs = self._signatures(cur, disbursement_id)
                queue = self._nonce_queue(cur, row, onchain_nonce)
                conn.rollback()
                return {
                    "safe": _cs(safe), "chain_id": self.settings.chain_id, "safe_tx_hash": row["safe_tx_hash"],
                    "nonce": int(row["safe_nonce"]), "sub_status": row["sub_status"],
                    "typed_data": typed_data(safe, self.settings.chain_id, tx),
                    "transfers": [{"to": _cs(t.to), "amount_units": str(t.amount_units)} for t in transfers],
                    "signatures": [{"signer": _cs(s["signer_address"]), "signed_at": s["signed_at"].isoformat()}
                                   for s in sigs],
                    "threshold": threshold, "owners": [_cs(o) for o in owners],
                    **queue,
                }
        finally:
            put_conn(conn)

    def pending(self, tenant_id: str, supplier_id: Optional[str] = None) -> list[dict]:
        """USDC payouts released but not yet on-chain, oldest nonce first.

        The pay page lists these so an owner continues an existing payout rather
        than preparing a new one: every new one takes the next Safe nonce and has
        to wait behind all the earlier ones.
        """
        conn = get_conn()
        try:
            with _dictcur(conn) as cur:
                cur.execute(
                    """SELECT d.disbursement_id::text AS disbursement_id, d.invoice_id::text AS invoice_id,
                              d.supplier_id::text AS supplier_id, fi.invoice_number, d.amount, d.currency_code,
                              c.safe_nonce, c.sub_status, d.created_at,
                              (SELECT count(*) FROM crypto_payout_signatures s
                                WHERE s.disbursement_id = d.disbursement_id) AS signatures,
                              tw.safe_threshold AS threshold
                         FROM fact_disbursements d
                         JOIN crypto_disbursement_details c ON c.disbursement_id = d.disbursement_id
                         LEFT JOIN treasury_wallets tw ON tw.id = c.treasury_wallet_id
                         LEFT JOIN fact_invoices fi ON fi.invoice_id = d.invoice_id
                        WHERE d.tenant_id = %s AND c.sub_status = ANY(%s)
                          AND (%s::uuid IS NULL OR d.supplier_id = %s::uuid)
                        ORDER BY c.safe_nonce NULLS LAST, d.created_at""",
                    (tenant_id, list(NONCE_HOLDING), supplier_id, supplier_id))
                rows = cur.fetchall()
            conn.rollback()
            return [{
                "disbursement_id": r["disbursement_id"], "invoice_id": r["invoice_id"],
                "supplier_id": r["supplier_id"], "invoice_number": r["invoice_number"],
                "amount": str(r["amount"]), "currency": (r["currency_code"] or "").strip() or None,
                "nonce": int(r["safe_nonce"]) if r["safe_nonce"] is not None else None,
                "sub_status": r["sub_status"], "signatures": int(r["signatures"]),
                "threshold": r["threshold"], "created_at": r["created_at"].isoformat(),
            } for r in rows]
        finally:
            put_conn(conn)

    # ── nonce order ─────────────────────────────────────────────────────────
    def _nonce_queue(self, cur, row: dict, onchain_nonce: Optional[int]) -> dict:
        """Where this payout stands in the Safe's queue.

        A Safe executes transactions strictly in nonce order, so a payout built at
        nonce 5 cannot go through until nonces up to 4 have. Two cases block it:

          waiting_for   an earlier payout still holds a lower nonce: sign or
                        cancel that one first (it is returned so the UI can link to it)
          nonce_gap     no payout holds the lower nonce any more (it was cancelled
                        or expired), so nothing will ever fill it: rebuild this
                        payout at the next free nonce (`repropose`)
        """
        out = {"safe_nonce_onchain": onchain_nonce, "waiting_for": None, "nonce_gap": False,
               "next_free_nonce": None, "demo_settlement": self.settings.demo_settlement()}
        if onchain_nonce is None or row["safe_nonce"] is None or row["sub_status"] not in SIGNABLE:
            return out
        mine = int(row["safe_nonce"])
        if self.settings.demo_settlement():
            # Demo settlements never advance the Safe's nonce, so nothing queues:
            # a payout either sits at the Safe's current nonce or must be rebuilt there.
            if mine != onchain_nonce:
                out["nonce_gap"] = True
                out["next_free_nonce"] = onchain_nonce
            return out
        if mine <= onchain_nonce:
            return out
        cur.execute(
            """SELECT c.disbursement_id::text AS disbursement_id, c.safe_nonce, c.sub_status,
                      d.invoice_id::text AS invoice_id, d.amount, d.supplier_id::text AS supplier_id
                 FROM crypto_disbursement_details c JOIN fact_disbursements d ON d.disbursement_id = c.disbursement_id
                WHERE c.treasury_wallet_id = %s AND c.disbursement_id <> %s AND c.sub_status = ANY(%s)
                  AND c.safe_nonce >= %s AND c.safe_nonce < %s
                ORDER BY c.safe_nonce LIMIT 1""",
            (row["treasury_wallet_id"], row["disbursement_id"], list(NONCE_HOLDING), onchain_nonce, mine))
        blocker = cur.fetchone()
        if blocker:
            out["waiting_for"] = {
                "disbursement_id": blocker["disbursement_id"], "nonce": int(blocker["safe_nonce"]),
                "sub_status": blocker["sub_status"], "invoice_id": blocker["invoice_id"],
                "supplier_id": blocker["supplier_id"], "amount": str(blocker["amount"]),
            }
        else:
            out["nonce_gap"] = True
            out["next_free_nonce"] = self._free_nonce(cur, row["treasury_wallet_id"], row["disbursement_id"],
                                                      onchain_nonce)
        return out

    def _free_nonce(self, cur, treasury_wallet_id, disbursement_id: str, onchain_nonce: int) -> int:
        """Lowest nonce at or above the Safe's on-chain nonce that no other in-flight payout holds.

        In demo settlement the Safe's nonce never moves, so it is always the Safe's own nonce.
        """
        if self.settings.demo_settlement():
            return onchain_nonce
        cur.execute(
            """SELECT c.safe_nonce FROM crypto_disbursement_details c
                WHERE c.treasury_wallet_id = %s AND c.disbursement_id <> %s AND c.sub_status = ANY(%s)
                  AND c.safe_nonce >= %s""",
            (treasury_wallet_id, disbursement_id, list(NONCE_HOLDING), onchain_nonce))
        held = {int(r["safe_nonce"]) for r in cur.fetchall()}
        n = onchain_nonce
        while n in held:
            n += 1
        return n

    def repropose(self, tenant_id: str, disbursement_id: str, actor: Optional[str]) -> dict:
        """Rebuild an unsubmitted payout at the next free nonce.

        Used when the payout waits on a nonce nothing will ever fill (an earlier
        payout was cancelled). The transaction hash changes with the nonce, so any
        signatures already collected are for a transaction that can no longer run:
        they are removed and the owners sign again.
        """
        conn = get_conn()
        conn.autocommit = False
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id, lock=True)
                if row["sub_status"] not in SIGNABLE:
                    raise error(409, "NOT_SIGNABLE", f"Payout is {row['sub_status']}; it cannot be rebuilt.")
                custody = self.custody()
                safe = row["remitter_account_number"]
                info = custody.info(safe)
                nonce = self._free_nonce(cur, row["treasury_wallet_id"], disbursement_id, info.nonce)
                old_nonce = int(row["safe_nonce"]) if row["safe_nonce"] is not None else None
                if old_nonce == nonce:
                    conn.rollback()
                    return {"disbursement_id": disbursement_id, "nonce": nonce, "changed": False}
                transfers = [Transfer(t["to_address"], t["amount_units"]) for t in expected_transfers(cur, disbursement_id)]
                tx = custody.build_payout(safe, transfers, nonce)
                tx_hash = "0x" + custody.tx_hash(safe, tx).hex()
                cur.execute("DELETE FROM crypto_payout_signatures WHERE disbursement_id=%s", (disbursement_id,))
                cur.execute(
                    """UPDATE crypto_disbursement_details SET safe_tx_hash=%s, safe_nonce=%s, sub_status='PROPOSED',
                           updated_at=now() WHERE disbursement_id=%s""", (tx_hash, nonce, disbursement_id))
                audit(cur, tenant_id, "fact_disbursements", disbursement_id,
                      {"safe_nonce": old_nonce, "safe_tx_hash": row["safe_tx_hash"]},
                      {"event": "reproposed", "safe_nonce": nonce, "safe_tx_hash": tx_hash,
                       "signatures_cleared": True}, actor)
                conn.commit()
                return {"disbursement_id": disbursement_id, "nonce": nonce, "safe_tx_hash": tx_hash, "changed": True}
        except HTTPException:
            conn.rollback()
            raise
        except SafeTxRefused as e:
            conn.rollback()
            raise error(400, e.code, str(e))
        finally:
            put_conn(conn)

    def add_signature(self, tenant_id: str, disbursement_id: str, signer: str, signature: str,
                      actor: Optional[str]) -> dict:
        conn = get_conn()
        conn.autocommit = False
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id, lock=True)
                if row["sub_status"] not in SIGNABLE:
                    raise error(409, "NOT_SIGNABLE", f"Payout is {row['sub_status']}; it can no longer be signed.")
                custody = self.custody()
                tx, _, safe = self._tx(cur, row)
                if "0x" + custody.tx_hash(safe, tx).hex() != row["safe_tx_hash"]:
                    raise error(409, "TX_CHANGED", "The payout's transaction no longer matches what was proposed.")
                try:
                    recovered = custody.recover_signer(safe, tx, signature)
                except SafeTxRefused as e:
                    raise error(400, e.code, str(e))
                if not signer or recovered.lower() != signer.lower():
                    raise error(400, "SIGNATURE_MISMATCH", "The signature was not made by the stated signer.")
                info = custody.info(safe)
                cur.execute("SELECT * FROM treasury_wallets WHERE id=%s", (row["treasury_wallet_id"],))
                self._sync_safe(cur, tenant_id, cur.fetchone(), info, actor)
                if recovered.lower() not in [o.lower() for o in info.owners]:
                    conn.commit()
                    raise error(403, "NOT_A_SAFE_OWNER", f"{recovered} is not an owner of the treasury Safe.")
                if info.nonce > int(row["safe_nonce"]):
                    _fail(cur, tenant_id, row, "REPLACED",
                          f"Safe nonce {row['safe_nonce']} was used by another transaction", actor)
                    conn.commit()
                    raise error(409, "REPLACED", "This Safe nonce was already used; start the payment again.")

                cur.execute(
                    """INSERT INTO crypto_payout_signatures (disbursement_id, signer_address, user_id, signature)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (disbursement_id, signer_address)
                       DO UPDATE SET signature=EXCLUDED.signature, user_id=EXCLUDED.user_id, signed_at=now()""",
                    (disbursement_id, Web3.to_checksum_address(recovered), actor, signature))
                owners = {o.lower() for o in info.owners}
                valid = [s for s in self._signatures(cur, disbursement_id) if s["signer_address"].lower() in owners]
                sub_status = "READY" if len(valid) >= info.threshold else "PARTIALLY_SIGNED"
                cur.execute("UPDATE crypto_disbursement_details SET sub_status=%s, updated_at=now() WHERE disbursement_id=%s",
                            (sub_status, disbursement_id))
                audit(cur, tenant_id, "fact_disbursements", disbursement_id, {"sub_status": row["sub_status"]},
                      {"event": "safe_signature", "signer": recovered, "signatures": len(valid),
                       "threshold": info.threshold, "sub_status": sub_status}, actor)
                result = {"sub_status": sub_status, "signatures": len(valid), "threshold": info.threshold}
                if sub_status == "READY":
                    result.update(self._relay(cur, tenant_id, {**row, "sub_status": "READY"}, tx, safe, info,
                                              [(s["signer_address"], s["signature"]) for s in valid], actor))
                conn.commit()
                if result.get("simulated"):
                    _match_demo_settlement()
                    result["sub_status"] = self._sub_status(disbursement_id) or result["sub_status"]
                return result
        except HTTPException:
            conn.rollback()
            raise
        finally:
            put_conn(conn)

    def _relay(self, cur, tenant_id: str, row: dict, tx: dict, safe: str, info: SafeInfo,
               signatures: list[tuple[str, str]], actor: Optional[str]) -> dict:
        """Submit a READY payout. Failures leave it READY with a reason; retrying is
        safe because the Safe nonce lets the transaction execute only once."""
        disbursement_id = str(row["disbursement_id"])

        def hold(reason: str) -> dict:
            cur.execute("UPDATE crypto_disbursement_details SET failure_reason=%s, updated_at=now() "
                        "WHERE disbursement_id=%s", (reason, disbursement_id))
            return {"sub_status": "READY", "message": reason}

        if self.settings.demo_settlement() and info.nonce != int(row["safe_nonce"]):
            return hold(f"Demo settlement runs at the Safe's current nonce {info.nonce}, but this payout was signed at "
                        f"nonce {row['safe_nonce']}: rebuild it at nonce {info.nonce} and sign again.")
        if info.nonce < int(row["safe_nonce"]):
            return hold(f"Queued behind Safe nonce {info.nonce}; the earlier Safe transaction must execute first.")
        try:
            balance = self.custody().balance(safe)
        except Exception:
            balance = None
        if balance is not None and balance < _units(row["gross_units"]):
            return hold("INSUFFICIENT_TREASURY_BALANCE: fund the treasury Safe, then add a signature again to retry.")
        if self.settings.demo_settlement():
            return self._demo_settle(cur, tenant_id, row, tx, safe, signatures, actor)
        try:
            tx_hash = self.custody().execute(safe, tx, signatures)
        except (RelayerError, SafeTxRefused) as e:
            logger.warning("[crypto] relay failed for %s: %s", disbursement_id, e)
            return hold(f"{getattr(e, 'code', 'RELAY_FAILED')}: {e}")
        cur.execute(
            """UPDATE crypto_disbursement_details SET sub_status='SUBMITTED', tx_hash=%s, submitted_at=now(),
                   failure_reason=NULL, updated_at=now() WHERE disbursement_id=%s""", (tx_hash, disbursement_id))
        cur.execute(
            """UPDATE fact_disbursements SET status_message='Submitted on-chain; waiting for confirmations',
                   bank_tran_ref_no=%s, updated_at=now() WHERE disbursement_id=%s""", (tx_hash, disbursement_id))
        audit(cur, tenant_id, "fact_disbursements", disbursement_id, {"sub_status": "READY"},
              {"event": "submitted", "sub_status": "SUBMITTED", "tx_hash": tx_hash}, actor or "system:relayer")
        return {"sub_status": "SUBMITTED", "tx_hash": tx_hash, "explorer": explorer_tx_url(tx_hash)}

    def _demo_settle(self, cur, tenant_id: str, row: dict, tx: dict, safe: str,
                     signatures: list[tuple[str, str]], actor: Optional[str]) -> dict:
        """Demo settlement (CRYPTO_SETTLEMENT=demo): everything real except the broadcast.

        1. The fully signed Safe transaction is dry-run against the real Safe on
           the chain (`eth_call`): the contract checks the owners' signatures, the
           nonce and the threshold, and runs the USDC transfer(s) against the
           Safe's real balance. Any failure holds the payout exactly as a failed
           broadcast would.
        2. Instead of broadcasting, the USDC Transfer events the transaction would
           emit are recorded in chain_transfers, flagged `simulated`, under a
           deterministic demo transaction id. From here the real pipeline runs
           unchanged: the matcher matches them leg by leg, confirmations count up
           with real blocks, the bill is settled, the Tally voucher is queued and
           every step is audited.
        No USDC moves and no gas is spent; every screen labels it as simulated.
        """
        disbursement_id = str(row["disbursement_id"])
        custody = self.custody()
        try:
            custody.simulate(safe, tx, signatures)
        except SafeTxRefused as e:
            cur.execute("UPDATE crypto_disbursement_details SET failure_reason=%s, updated_at=now() "
                        "WHERE disbursement_id=%s", (f"{e.code}: {e}", disbursement_id))
            return {"sub_status": "READY", "message": f"{e.code}: {e}"}
        from web3 import Web3
        demo_hash = Web3.keccak(text=f"onsumm-demo-settlement:{disbursement_id}:{row['safe_tx_hash']}").hex()
        demo_hash = demo_hash if demo_hash.startswith("0x") else "0x" + demo_hash
        head = custody.chain.latest_block()
        usdc = Web3.to_checksum_address(self.settings.usdc_address)
        for i, leg in enumerate(expected_transfers(cur, disbursement_id)):
            cur.execute(
                """INSERT INTO chain_transfers
                       (chain, tx_hash, log_index, block_number, block_hash, block_time, token_contract, from_address,
                        to_address, amount_units, tenant_id, direction, match_type, status, confirmations, removed,
                        simulated)
                   VALUES (%s, %s, %s, %s, NULL, now(), %s, %s, %s, %s, %s, 'OUT', NULL, 'UNMATCHED', %s, false, true)
                   ON CONFLICT (chain, tx_hash, log_index) DO NOTHING""",
                (self.settings.chain, demo_hash, i, head, usdc, _cs(safe), _cs(leg["to_address"]),
                 int(leg["amount_units"]), tenant_id,
                 # Nothing was mined, so there are no blocks to wait for: the demo
                 # record is final at once and the matcher settles it straight away.
                 self.settings.confirmations))
        cur.execute(
            """UPDATE crypto_disbursement_details SET sub_status='SUBMITTED', tx_hash=%s, submitted_at=now(),
                   simulated=true, block_number=%s, confirmations=%s, failure_reason=NULL, updated_at=now()
             WHERE disbursement_id=%s""",
            (demo_hash, head, self.settings.confirmations, disbursement_id))
        cur.execute(
            """UPDATE fact_disbursements SET status_message=%s, bank_tran_ref_no=%s, updated_at=now()
                WHERE disbursement_id=%s""",
            ("Demo settlement: validated against the Safe contract on-chain; not broadcast",
             demo_hash, disbursement_id))
        audit(cur, tenant_id, "fact_disbursements", disbursement_id, {"sub_status": "READY"},
              {"event": "submitted", "sub_status": "SUBMITTED", "tx_hash": demo_hash, "simulated": True,
               "validated_by": "eth_call execTransaction on the Safe"}, actor or "system:demo-relayer")
        return {"sub_status": "SUBMITTED", "tx_hash": demo_hash, "explorer": None, "simulated": True}

    def _sub_status(self, disbursement_id: str) -> Optional[str]:
        conn = get_conn()
        try:
            with _dictcur(conn) as cur:
                cur.execute("SELECT sub_status FROM crypto_disbursement_details WHERE disbursement_id=%s",
                            (disbursement_id,))
                row = cur.fetchone()
            conn.rollback()
            return row["sub_status"] if row else None
        finally:
            put_conn(conn)

    def retry_ready(self, disbursement_id: str, tenant_id: str) -> dict:
        """Re-attempt relaying a READY payout (scheduler or admin)."""
        conn = get_conn()
        conn.autocommit = False
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id, lock=True)
                if row["sub_status"] != "READY":
                    conn.rollback()
                    return {"sub_status": row["sub_status"]}
                tx, _, safe = self._tx(cur, row)
                info = self.custody().info(safe)
                owners = {o.lower() for o in info.owners}
                valid = [(s["signer_address"], s["signature"]) for s in self._signatures(cur, disbursement_id)
                         if s["signer_address"].lower() in owners]
                if len(valid) < info.threshold:
                    cur.execute("UPDATE crypto_disbursement_details SET sub_status='PARTIALLY_SIGNED' WHERE disbursement_id=%s",
                                (disbursement_id,))
                    conn.commit()
                    return {"sub_status": "PARTIALLY_SIGNED"}
                out = self._relay(cur, tenant_id, row, tx, safe, info, valid, "system:relayer")
                conn.commit()
                if out.get("simulated"):
                    _match_demo_settlement()
                return out
        finally:
            put_conn(conn)

    # ── status / cancel ─────────────────────────────────────────────────────
    def status(self, tenant_id: str, disbursement_id: str) -> dict:
        conn = get_conn()
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id)
                sigs = self._signatures(cur, disbursement_id)
                cur.execute("SELECT safe_threshold FROM treasury_wallets WHERE id=%s", (row["treasury_wallet_id"],))
                threshold = (cur.fetchone() or {}).get("safe_threshold")
                cur.execute("SELECT * FROM chain_transfers WHERE matched_disbursement_id=%s ORDER BY log_index",
                            (disbursement_id,))
                transfers = [{**jsonable(t), "id": str(t["id"]),
                              "explorer": None if t.get("simulated") else explorer_tx_url(t["tx_hash"])}
                             for t in cur.fetchall()]
                conn.rollback()
            return {
                "disbursement_id": disbursement_id, "invoice_id": str(row["invoice_id"]),
                "status": row["status"], "sub_status": row["sub_status"],
                "amount": f"{Decimal(row['amount']):.2f}", "currency": (row.get("currency_code") or "").strip(),
                "gross_units": str(_units(row["gross_units"])), "net_units": str(_units(row["amount_units"])),
                "withholding_units": str(_units(row["withholding_units"])),
                "safe": _cs(row["remitter_account_number"]), "safe_tx_hash": row["safe_tx_hash"],
                "nonce": int(row["safe_nonce"]) if row["safe_nonce"] is not None else None,
                "signatures": [{"signer": _cs(s["signer_address"]), "signed_at": s["signed_at"].isoformat()} for s in sigs],
                "threshold": threshold, "tx_hash": row["tx_hash"],
                # A demo settlement was never broadcast: there is nothing on the explorer.
                "explorer": explorer_tx_url(row["tx_hash"]) if row["tx_hash"] and not row.get("simulated") else None,
                "simulated": bool(row.get("simulated")),
                "block_number": row["block_number"], "confirmations": row["confirmations"] or 0,
                "confirmations_target": self.settings.confirmations,
                "failure_reason": row["failure_reason"] or (row["status_message"] if row["status"] == "FAILED" else None),
                "transfers": transfers,
            }
        finally:
            put_conn(conn)

    def keep_only(self, tenant_id: str, disbursement_id: str, actor: Optional[str]) -> dict:
        """Cancel this vendor's other unsent USDC payouts and keep this one.

        Duplicate payouts pile up when a payout is prepared again instead of
        reopened, and each one holds a Safe nonce the others queue behind. This
        cancels every other payout for the same vendor and Safe that has not been
        submitted yet, then rebuilds this one at the Safe's next free nonce so it
        can run first. Rebuilding changes the transaction, so signatures already
        collected for it are cleared and the owners sign once more.
        """
        conn = get_conn()
        conn.autocommit = False
        cancelled: list[str] = []
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id, lock=True)
                if row["sub_status"] not in SIGNABLE:
                    raise error(409, "NOT_SIGNABLE", f"Payout is {row['sub_status']}; it cannot be kept as the only one.")
                cur.execute(
                    """SELECT c.disbursement_id::text AS id
                         FROM crypto_disbursement_details c
                         JOIN fact_disbursements f ON f.disbursement_id = c.disbursement_id
                        WHERE f.tenant_id = %s AND f.supplier_id = %s AND c.treasury_wallet_id = %s
                          AND c.disbursement_id <> %s AND c.sub_status = ANY(%s)
                        ORDER BY c.safe_nonce NULLS LAST""",
                    (tenant_id, row["supplier_id"], row["treasury_wallet_id"], disbursement_id, list(PRE_SUBMIT)))
                for other in [r["id"] for r in cur.fetchall()]:
                    orow = self._load(cur, tenant_id, other, lock=True)
                    _fail(cur, tenant_id, orow, "REJECTED",
                          f"Cancelled: superseded by payout {disbursement_id} (kept as the only one)", actor)
                    cancelled.append(other)
                conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            put_conn(conn)
        rebuilt = self.repropose(tenant_id, disbursement_id, actor)
        return {"disbursement_id": disbursement_id, "cancelled": cancelled, "nonce": rebuilt.get("nonce"),
                "rebuilt": bool(rebuilt.get("changed")), "signatures_cleared": bool(rebuilt.get("changed"))}

    def cancel(self, tenant_id: str, disbursement_id: str, reason: str, actor: Optional[str]) -> dict:
        conn = get_conn()
        conn.autocommit = False
        try:
            with _dictcur(conn) as cur:
                row = self._load(cur, tenant_id, disbursement_id, lock=True)
                if row["sub_status"] not in PRE_SUBMIT:
                    raise error(409, "TOO_LATE_TO_CANCEL",
                                f"Payout is {row['sub_status']}; it is already on-chain and cannot be cancelled.")
                _fail(cur, tenant_id, row, "REJECTED", f"Cancelled: {reason or 'no reason given'}", actor)
                conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            put_conn(conn)
        return self.status(tenant_id, disbursement_id)


# ── Seam for the chain watcher / matcher (caller commits) ─────────────────────

def expected_transfers(cur, disbursement_id: str) -> list[dict]:
    """The USDC transfers this payout must produce on-chain: the vendor leg and,
    when tax is withheld, the TAX-wallet leg of the same MultiSend."""
    cur.execute(
        """SELECT c.amount_units, c.withholding_units, w.address AS vendor_address, t.address AS tax_address
             FROM crypto_disbursement_details c
             JOIN supplier_wallets w ON w.id = c.supplier_wallet_id
             LEFT JOIN treasury_wallets t ON t.id = c.tax_wallet_id
            WHERE c.disbursement_id=%s""", (disbursement_id,))
    r = cur.fetchone()
    if not r:
        return []
    r = dict(r) if not isinstance(r, dict) else r
    out = [{"to_address": Web3.to_checksum_address(r["vendor_address"]), "amount_units": _units(r["amount_units"]),
            "match_type": "PAYOUT"}]
    if _units(r["withholding_units"]) > 0 and r["tax_address"]:
        out.append({"to_address": Web3.to_checksum_address(r["tax_address"]),
                    "amount_units": _units(r["withholding_units"]), "match_type": "TAX"})
    return out


def _fail(cur, tenant_id: str, row: dict, sub_status: str, reason: str, actor: Optional[str]) -> None:
    disbursement_id = str(row["disbursement_id"])
    cur.execute("""UPDATE fact_disbursements SET status='FAILED', status_message=%s, updated_at=now(), updated_by=%s
                    WHERE disbursement_id=%s""", (reason, actor, disbursement_id))
    cur.execute("""UPDATE crypto_disbursement_details SET sub_status=%s, failure_reason=%s, updated_at=now()
                    WHERE disbursement_id=%s""", (sub_status, reason, disbursement_id))
    audit(cur, tenant_id, "fact_disbursements", disbursement_id,
          {"status": row.get("status"), "sub_status": row.get("sub_status")},
          {"event": "failed", "status": "FAILED", "sub_status": sub_status, "reason": reason}, actor)


def _match_demo_settlement() -> None:
    """Run the real on-chain matcher now, so a demo settlement is matched, the bill
    settled and the voucher queued in the same request rather than on the next
    watcher pass. Errors are logged: the scheduled matcher retries every few seconds."""
    try:
        from .recon import OnchainMatcher
        from ...integrations.chains.evm import get_chain
        conn = get_conn()
        try:
            OnchainMatcher(get_chain()).run(conn)
            conn.commit()
        finally:
            put_conn(conn)
    except Exception:
        logger.exception("[crypto] immediate match of a demo settlement failed; the scheduled matcher will retry")


def mark_confirming(conn, disbursement_id: str, tx_hash: str, block_number: int, confirmations: int) -> None:
    with _dictcur(conn) as cur:
        cur.execute(
            """UPDATE crypto_disbursement_details
                  SET sub_status = CASE WHEN sub_status IN ('READY','SUBMITTED','CONFIRMING') THEN 'CONFIRMING'
                                        ELSE sub_status END,
                      tx_hash = COALESCE(tx_hash, %s), block_number=%s, confirmations=%s, updated_at=now()
                WHERE disbursement_id=%s AND sub_status NOT IN ('CONFIRMED','REJECTED','EXPIRED','REVERTED','REPLACED')""",
            (tx_hash, block_number, confirmations, disbursement_id))


def mark_confirmed(conn, tenant_id: str, disbursement_id: str, tx_hash: str, block_number: int) -> bool:
    """Settle the bill for a confirmed payout. Idempotent: returns False if already settled."""
    with _dictcur(conn) as cur:
        cur.execute("""SELECT * FROM fact_disbursements WHERE disbursement_id=%s AND tenant_id=%s FOR UPDATE""",
                    (disbursement_id, tenant_id))
        d = cur.fetchone()
        if not d or d["status"] == "SUCCESS":
            return False
        cur.execute("SELECT total_amount, amount_paid FROM fact_invoices WHERE invoice_id=%s FOR UPDATE",
                    (d["invoice_id"],))
        inv = cur.fetchone()
        new_paid = Decimal(inv["amount_paid"] or 0) + Decimal(d["amount"])
        outstanding = max(Decimal(0), Decimal(inv["total_amount"] or 0) - new_paid)
        new_status = "paid" if outstanding <= Decimal("0.01") else "partial"
        cur.execute("UPDATE fact_invoices SET payment_status=%s, amount_paid=%s, outstanding_amount=%s WHERE invoice_id=%s",
                    (new_status, new_paid, outstanding, d["invoice_id"]))
        currency = (d.get("currency_code") or "").strip()
        cur.execute(
            """INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment)
               VALUES (%s,%s,99,%s,%s,%s,%s)""",
            (d["invoice_id"], tenant_id, "Admin", d["initiated_by"] or "Admin", f"payment_{new_status}",
             f"USDC payout {currency} {Decimal(d['amount']):,.2f} via tx {tx_hash}"))
        cur.execute(
            """UPDATE fact_disbursements SET status='SUCCESS', status_message='Paid and reconciled on-chain',
                   bank_tran_ref_no=%s, bank_rrn=%s, updated_at=now() WHERE disbursement_id=%s""",
            (tx_hash, tx_hash, disbursement_id))
        cur.execute(
            """UPDATE crypto_disbursement_details SET sub_status='CONFIRMED', tx_hash=%s, block_number=%s,
                   confirmed_at=now(), failure_reason=NULL, updated_at=now() WHERE disbursement_id=%s""",
            (tx_hash, block_number, disbursement_id))
        cur.execute("UPDATE crypto_withholdings SET status='WITHHELD' WHERE disbursement_id=%s AND amount_units > 0",
                    (disbursement_id,))
        audit(cur, tenant_id, "fact_disbursements", disbursement_id, {"status": d["status"]},
              {"event": "confirmed", "status": "SUCCESS", "sub_status": "CONFIRMED", "tx_hash": tx_hash,
               "block_number": block_number, "payment_status": new_status}, "system:onchain-matcher")
        try:
            from .ledger import enqueue_payout_posting
        except ImportError:
            logger.info("[crypto] ledger module not installed; no voucher queued for %s", disbursement_id)
        else:
            cur.execute("SAVEPOINT crypto_ledger")
            try:
                enqueue_payout_posting(cur, tenant_id, disbursement_id)
                cur.execute("RELEASE SAVEPOINT crypto_ledger")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT crypto_ledger")
                logger.exception("[crypto] could not queue voucher for %s", disbursement_id)
    return True


def mark_failed(conn, tenant_id: str, disbursement_id: str, sub_status: str, reason: str) -> bool:
    """Fail a payout (e.g. REVERTED on-chain). Never un-settles a SUCCESS. Caller commits."""
    with _dictcur(conn) as cur:
        cur.execute("""SELECT f.disbursement_id, f.status, c.sub_status FROM fact_disbursements f
                         JOIN crypto_disbursement_details c ON c.disbursement_id=f.disbursement_id
                        WHERE f.disbursement_id=%s AND f.tenant_id=%s FOR UPDATE OF f""", (disbursement_id, tenant_id))
        row = cur.fetchone()
        if not row or row["status"] in TERMINAL:
            return False
        _fail(cur, tenant_id, row, sub_status, reason, "system:onchain-matcher")
    return True


def retry_ready_payouts(limit: int = 10) -> int:
    """Scheduler hook: retry relaying READY payouts (relayer was out of gas, RPC blip, queued nonce)."""
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute("""SELECT c.disbursement_id, c.tenant_id FROM crypto_disbursement_details c
                            WHERE c.sub_status='READY' ORDER BY c.safe_nonce LIMIT %s""", (limit,))
            rows = cur.fetchall()
        conn.rollback()
    finally:
        put_conn(conn)
    done = 0
    svc = PayoutService()
    for r in rows:
        try:
            if svc.retry_ready(str(r["disbursement_id"]), str(r["tenant_id"])).get("sub_status") == "SUBMITTED":
                done += 1
        except Exception:
            logger.exception("[crypto] retry of %s failed", r["disbursement_id"])
    return done
