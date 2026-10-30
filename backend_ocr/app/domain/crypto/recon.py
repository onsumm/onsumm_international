"""On-chain reconciliation and the bill checks that run before a crypto payout.

OnchainMatcher
──────────────
Turns `chain_transfers` rows (written by the watcher) into payout outcomes.
An outgoing transfer is matched to a payout only on an exact rule:

    same tx_hash as the payout's executed Safe transaction
    token contract = the configured USDC contract
    destination and amount (6-decimal units) = one of the payout's expected legs

A payout has one expected leg (net to the vendor) or two when tax was withheld
(net to the vendor, withheld amount to the tenant's TAX wallet — one Safe
MultiSend). It is CONFIRMED only when every expected leg is matched, not
removed by a reorg, and at least CONFIRMATIONS deep. Anything else raises a
Review Hub finding and leaves the bill PENDING; nothing is settled on a guess.

An outgoing transfer with no payout behind it means money left a client
treasury outside the approval flow: UNMATCHED_OUTBOUND_TRANSFER, critical.

The payout state changes themselves live in payouts.py (the payments half of
the rail); this module only decides *when* to call them.

Bill checks
───────────
`ContractRateCheck` compares invoice lines with `vendor_contract_rates`
(OVERBILLED_VS_CONTRACT). `check_invoice_wallet` looks for wallet addresses
printed on the invoice and flags any that is not the vendor's verified wallet
(WALLET_MISMATCH): an address on an invoice is never paid, only compared.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Optional

import psycopg2.extras

from .findings import raise_finding
from .settings import get_settings

logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
STUCK_AFTER_MINUTES = 30


# ── Pure matching logic (unit-tested without a database) ────────────────────

@dataclass
class ExpectedLeg:
    to_address: str
    amount_units: int
    match_type: str  # PAYOUT | TAX


@dataclass
class TransferRow:
    id: str
    tx_hash: str
    token_contract: str
    to_address: str
    amount_units: int
    confirmations: int
    removed: bool = False


@dataclass
class MatchOutcome:
    matches: dict[str, str] = field(default_factory=dict)      # transfer id -> PAYOUT | TAX
    mismatches: list[str] = field(default_factory=list)        # transfer ids
    missing: list[ExpectedLeg] = field(default_factory=list)
    decision: str = "PENDING"                                  # CONFIRMED | CONFIRMING | MISMATCH | PENDING


def match_payout(expected: list[ExpectedLeg], transfers: list[TransferRow], usdc_address: str,
                 confirmations_target: int) -> MatchOutcome:
    """Match one payout's transfers (all from its tx) against its expected legs."""
    out = MatchOutcome()
    remaining = list(expected)
    live = [t for t in transfers if not t.removed]
    for t in live:
        hit = None
        if t.token_contract.lower() == usdc_address.lower():
            hit = next((e for e in remaining
                        if e.to_address.lower() == t.to_address.lower() and int(e.amount_units) == int(t.amount_units)),
                       None)
        if hit is None:
            out.mismatches.append(t.id)
            continue
        remaining.remove(hit)
        out.matches[t.id] = hit.match_type
    out.missing = remaining

    if out.mismatches:
        out.decision = "MISMATCH"
    elif not out.missing and out.matches:
        deep = all(t.confirmations >= confirmations_target for t in live if t.id in out.matches)
        out.decision = "CONFIRMED" if deep else "CONFIRMING"
    else:
        # Some legs not seen yet. A single-leg payout simply hasn't landed; a
        # MultiSend with one leg present and one absent stays PENDING until the
        # missing leg appears — the transaction is atomic, so a permanent gap
        # means the expected amounts are wrong, which `stale_partial` reports.
        out.decision = "PENDING"
    return out


# ── Expected legs ───────────────────────────────────────────────────────────

def _expected_from_tables(cur, disbursement_id: str) -> list[ExpectedLeg]:
    """Fallback for when payouts.expected_transfers is unavailable: rebuild the legs
    from crypto_disbursement_details (net to the vendor, withheld to the tax wallet)."""
    cur.execute(
        """SELECT cdd.amount_units, cdd.withholding_units, sw.address AS vendor_address, tw.address AS tax_address
             FROM crypto_disbursement_details cdd
             JOIN supplier_wallets sw ON sw.id = cdd.supplier_wallet_id
             LEFT JOIN treasury_wallets tw ON tw.id = cdd.tax_wallet_id
            WHERE cdd.disbursement_id = %s""",
        (disbursement_id,),
    )
    row = cur.fetchone()
    if not row:
        return []
    legs = [ExpectedLeg(row["vendor_address"], int(row["amount_units"]), "PAYOUT")]
    tax_units = int(row["withholding_units"] or 0)
    if tax_units > 0 and row["tax_address"]:
        legs.append(ExpectedLeg(row["tax_address"], tax_units, "TAX"))
    return legs


def expected_legs(cur, disbursement_id: str) -> list[ExpectedLeg]:
    try:
        from .payouts import expected_transfers  # payments half of the rail
    except ImportError:
        return _expected_from_tables(cur, disbursement_id)
    legs = []
    for leg in expected_transfers(cur, disbursement_id):
        get = leg.get if isinstance(leg, dict) else (lambda k, _l=leg: getattr(_l, k))
        legs.append(ExpectedLeg(get("to_address"), int(get("amount_units")), get("match_type")))
    return legs


def _payouts_api() -> Optional[dict[str, Callable]]:
    try:
        from . import payouts
    except ImportError:
        logger.warning("[onchain-recon] payouts module not available; matches recorded, payouts not advanced")
        return None
    return {name: getattr(payouts, name, None) for name in ("mark_confirming", "mark_confirmed", "mark_failed")}


# ── Matcher ─────────────────────────────────────────────────────────────────

class OnchainMatcher:
    def __init__(self, chain=None):
        self.chain = chain
        self.settings = get_settings()

    def run(self, conn) -> dict:
        stats = {"payouts_confirmed": 0, "outbound_unmatched": 0, "mismatches": 0, "inbound": 0, "reverted": 0}
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            self._match_outbound(conn, cur, stats)
            self._classify_inbound(cur, stats)
            conn.commit()
            if self.chain is not None:
                self._check_submitted(conn, cur, stats)
                conn.commit()
        return stats

    # Outgoing ---------------------------------------------------------------
    def _match_outbound(self, conn, cur, stats: dict) -> None:
        cur.execute(
            """SELECT DISTINCT ct.tx_hash
                 FROM chain_transfers ct
                 LEFT JOIN fact_disbursements d ON d.disbursement_id = ct.matched_disbursement_id
                WHERE ct.chain = %s AND ct.direction = 'OUT' AND NOT ct.removed
                  AND (ct.match_type IS NULL                       -- not yet classified
                       OR (ct.status = 'MATCHED' AND d.status IS DISTINCT FROM 'SUCCESS'))""",
            (self.settings.chain,),
        )
        for (tx_hash,) in [(r["tx_hash"],) for r in cur.fetchall()]:
            cur.execute(
                """SELECT ct.id::text AS id, ct.tx_hash, ct.token_contract, ct.to_address, ct.amount_units,
                          ct.confirmations, ct.removed, ct.status, ct.tenant_id::text AS tenant_id, ct.block_number
                     FROM chain_transfers ct WHERE ct.chain = %s AND ct.tx_hash = %s ORDER BY ct.log_index""",
                (self.settings.chain, tx_hash),
            )
            rows = cur.fetchall()
            cur.execute(
                """SELECT d.disbursement_id::text AS disbursement_id, d.tenant_id::text AS tenant_id,
                          d.invoice_id::text AS invoice_id, d.status
                     FROM crypto_disbursement_details cdd
                     JOIN fact_disbursements d ON d.disbursement_id = cdd.disbursement_id
                    WHERE lower(cdd.tx_hash) = lower(%s)""",
                (tx_hash,),
            )
            payout = cur.fetchone()
            if payout is None:
                self._unmatched_outbound(cur, rows, stats)
                continue
            if payout["status"] == "SUCCESS":
                continue
            if any(r["status"] == "MISMATCH" and not r["removed"] for r in rows):
                continue  # already under review via AMOUNT_MISMATCH; don't re-decide every tick
            outcome = match_payout(
                expected_legs(cur, payout["disbursement_id"]),
                [TransferRow(r["id"], r["tx_hash"], r["token_contract"], r["to_address"], int(r["amount_units"]),
                             int(r["confirmations"] or 0), bool(r["removed"])) for r in rows],
                self.settings.usdc_address, self.settings.confirmations,
            )
            self._record(cur, payout, rows, outcome, stats)
            conn.commit()
            self._advance(conn, payout, rows, outcome, stats)

    def _unmatched_outbound(self, cur, rows: list[dict], stats: dict) -> None:
        for r in rows:
            if r["removed"] or int(r["confirmations"] or 0) < self.settings.confirmations:
                # Give the relayer a few blocks to record its tx_hash before alarming;
                # the row stays unclassified and is looked at again next tick.
                continue
            # Classified once (match_type set), so the finding is filed once, not every tick.
            cur.execute("UPDATE chain_transfers SET status = 'UNMATCHED', match_type = 'UNKNOWN' WHERE id = %s",
                        (r["id"],))
            raise_finding(cur, r["tenant_id"], "UNMATCHED_OUTBOUND_TRANSFER", "CRITICAL", "transfer", r["id"],
                          "USDC left the treasury without an Onsumm-approved payout",
                          {"tx_hash": r["tx_hash"], "to": r["to_address"], "amount_units": str(r["amount_units"]),
                           "detail": "No approved payout produced this transaction. Check the Safe's history."})
            stats["outbound_unmatched"] += 1

    def _record(self, cur, payout: dict, rows: list[dict], outcome: MatchOutcome, stats: dict) -> None:
        for r in rows:
            if r["id"] in outcome.matches:
                cur.execute(
                    """UPDATE chain_transfers SET status = 'MATCHED', match_type = %s,
                              matched_disbursement_id = %s, matched_invoice_id = %s WHERE id = %s""",
                    (outcome.matches[r["id"]], payout["disbursement_id"], payout["invoice_id"], r["id"]),
                )
            elif r["id"] in outcome.mismatches:
                cur.execute(
                    """UPDATE chain_transfers SET status = 'MISMATCH', match_type = 'UNKNOWN',
                              matched_disbursement_id = %s, matched_invoice_id = %s WHERE id = %s""",
                    (payout["disbursement_id"], payout["invoice_id"], r["id"]),
                )
        if outcome.decision == "MISMATCH":
            stats["mismatches"] += 1
            raise_finding(cur, payout["tenant_id"], "AMOUNT_MISMATCH", "HIGH", "disbursement",
                          payout["disbursement_id"],
                          "On-chain transfer does not match the approved payout",
                          {"tx_hash": rows[0]["tx_hash"] if rows else None,
                           "mismatched_transfers": outcome.mismatches,
                           "missing_legs": [{"to": m.to_address, "amount_units": str(m.amount_units),
                                             "type": m.match_type} for m in outcome.missing],
                           "detail": "The bill stays pending until finance reviews the transaction."})

    def _advance(self, conn, payout: dict, rows: list[dict], outcome: MatchOutcome, stats: dict) -> None:
        api = _payouts_api()
        if api is None:
            return
        tx_hash = rows[0]["tx_hash"]
        block = max(int(r["block_number"]) for r in rows) if rows else None
        conf = min((int(r["confirmations"] or 0) for r in rows if r["id"] in outcome.matches), default=0)
        try:
            if outcome.decision == "CONFIRMED" and api["mark_confirmed"]:
                api["mark_confirmed"](conn, payout["tenant_id"], payout["disbursement_id"], tx_hash, block)
                stats["payouts_confirmed"] += 1
            elif outcome.decision == "CONFIRMING" and api["mark_confirming"]:
                api["mark_confirming"](conn, payout["disbursement_id"], tx_hash, block, conf)
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("[onchain-recon] could not advance payout %s", payout["disbursement_id"])

    # Incoming ---------------------------------------------------------------
    def _classify_inbound(self, cur, stats: dict) -> None:
        """Incoming USDC: from the tenant's own wallets = internal; from one of its
        vendors' wallets = a refund to review; anything else = treasury funding.
        (Receivable matching against sales invoices is a post-hackathon stretch.)"""
        cur.execute(
            """SELECT ct.id::text AS id, ct.tenant_id::text AS tenant_id, ct.from_address, ct.amount_units, ct.tx_hash,
                      EXISTS (SELECT 1 FROM treasury_wallets tw
                               WHERE tw.tenant_id = ct.tenant_id AND lower(tw.address) = lower(ct.from_address)) AS internal,
                      (SELECT sw.supplier_id::text FROM supplier_wallets sw
                        WHERE sw.tenant_id = ct.tenant_id AND lower(sw.address) = lower(ct.from_address) LIMIT 1) AS supplier_id
                 FROM chain_transfers ct
                WHERE ct.chain = %s AND ct.direction = 'IN' AND ct.match_type IS NULL AND NOT ct.removed""",
            (self.settings.chain,),
        )
        for r in cur.fetchall():
            if r["supplier_id"]:
                raise_finding(cur, r["tenant_id"], "UNMATCHED_INBOUND_TRANSFER", "MEDIUM", "transfer", r["id"],
                              "A vendor sent USDC back to the treasury",
                              {"tx_hash": r["tx_hash"], "from": r["from_address"],
                               "amount_units": str(r["amount_units"]), "supplier_id": r["supplier_id"],
                               "detail": "Record it as a refund or credit note against the vendor."})
                # Classified, so the finding is not re-filed every tick.
                cur.execute("UPDATE chain_transfers SET status = 'MISMATCH', match_type = 'UNKNOWN' WHERE id = %s",
                            (r["id"],))
            else:
                cur.execute("UPDATE chain_transfers SET status = 'MATCHED', match_type = 'FUNDING' WHERE id = %s",
                            (r["id"],))
            stats["inbound"] += 1

    # Submitted payouts ------------------------------------------------------
    def _check_submitted(self, conn, cur, stats: dict) -> None:
        """A submitted Safe transaction that reverted never emits Transfer logs, so
        the matcher would wait forever. Read its receipt instead."""
        cur.execute(
            """SELECT d.disbursement_id::text AS disbursement_id, d.tenant_id::text AS tenant_id, cdd.tx_hash,
                      cdd.submitted_at, (now() - cdd.submitted_at) > make_interval(mins => %s) AS stale
                 FROM crypto_disbursement_details cdd
                 JOIN fact_disbursements d ON d.disbursement_id = cdd.disbursement_id
                WHERE cdd.sub_status IN ('SUBMITTED', 'CONFIRMING') AND cdd.tx_hash IS NOT NULL
                  AND d.status = 'PENDING'
                  AND NOT cdd.simulated          -- a demo settlement has no receipt to read""",
            (STUCK_AFTER_MINUTES,),
        )
        api = _payouts_api()
        for p in cur.fetchall():
            try:
                receipt = self.chain.get_receipt(p["tx_hash"])
            except Exception:
                continue
            if receipt is not None and int(receipt.get("status", 1)) == 0:
                if api and api["mark_failed"]:
                    try:
                        api["mark_failed"](conn, p["tenant_id"], p["disbursement_id"], "REVERTED",
                                           "The Safe transaction reverted on-chain")
                        conn.commit()
                        stats["reverted"] += 1
                    except Exception:
                        conn.rollback()
                        logger.exception("[onchain-recon] could not fail payout %s", p["disbursement_id"])
            elif receipt is None and p["stale"]:
                raise_finding(cur, p["tenant_id"], "PAYOUT_STUCK", "HIGH", "disbursement", p["disbursement_id"],
                              "Payout submitted but not mined after 30 minutes",
                              {"tx_hash": p["tx_hash"], "detail": "Check the relayer's gas and the RPC provider."})


# ── Bill checks ─────────────────────────────────────────────────────────────

def _norm(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_WHOLE_BILL_KEYS = {"*", "total", "contract", "retainer", "bill"}


def contract_overbilling(lines: list[dict], rates: list[dict], subtotal: Optional[Decimal]) -> Optional[dict]:
    """Pure: compare bill lines with contract rates. Returns the overbilling detail or None.

    A line matches a rate when the normalised item_key equals, or is contained
    in, the normalised product name. With no matching line, a whole-bill rate
    ("*", "total", "contract", "retainer") — or the vendor's only rate — is
    compared against the bill subtotal.
    """
    over = Decimal("0")
    hits = []
    matched_any = False
    for line in lines:
        name = _norm(line.get("product_name"))
        rate = next((r for r in rates if _norm(r["item_key"]) not in ("",) and
                     _norm(r["item_key"]) not in _WHOLE_BILL_KEYS and
                     (_norm(r["item_key"]) == name or _norm(r["item_key"]) in name)), None)
        if rate is None:
            continue
        matched_any = True
        qty = Decimal(str(line.get("quantity") or 1))
        billed = Decimal(str(line.get("unit_price") if line.get("unit_price") is not None else line.get("line_total") or 0))
        contract = Decimal(str(rate["unit_price"]))
        if billed > contract + Decimal("0.005"):
            diff = ((billed - contract) * qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
            over += diff
            hits.append({"line": line.get("line_number"), "item": line.get("product_name"),
                         "billed_unit_price": str(billed), "contract_unit_price": str(contract),
                         "quantity": str(qty), "over_by": str(diff)})
    if not matched_any:
        whole = [r for r in rates if _norm(r["item_key"]) in _WHOLE_BILL_KEYS] or (rates if len(rates) == 1 else [])
        if whole and subtotal is not None:
            contract = Decimal(str(whole[0]["unit_price"]))
            billed = Decimal(str(subtotal))
            if billed > contract + Decimal("0.005"):
                over = (billed - contract).quantize(Decimal("0.01"), ROUND_HALF_UP)
                hits.append({"line": None, "item": whole[0]["item_key"], "billed": str(billed),
                             "contract": str(contract), "over_by": str(over)})
    if over <= 0:
        return None
    return {"over_by": str(over), "lines": hits}


class ContractRateCheck:
    def run(self, cur, tenant_id: str, invoice_id: str) -> Optional[dict]:
        cur.execute(
            """SELECT invoice_id::text AS invoice_id, supplier_id::text AS supplier_id, invoice_number,
                      currency_code, subtotal, total_amount,
                      COALESCE(stg_bill_id, archived_stg_bill_id)::text AS stg_bill_id
                 FROM fact_invoices WHERE invoice_id = %s AND tenant_id = %s""",
            (invoice_id, tenant_id),
        )
        bill = cur.fetchone()
        if not bill or not bill["supplier_id"]:
            return None
        cur.execute(
            """SELECT item_key, unit_price, currency_code FROM vendor_contract_rates
                WHERE tenant_id = %s AND supplier_id = %s
                  AND (valid_from IS NULL OR valid_from <= current_date)
                  AND (valid_to IS NULL OR valid_to >= current_date)""",
            (tenant_id, bill["supplier_id"]),
        )
        rates = cur.fetchall()
        if not rates:
            return None
        lines = []
        if bill["stg_bill_id"]:
            cur.execute(
                """SELECT line_number, product_name, quantity, unit_price, line_total
                     FROM stg_bill_line_items WHERE stg_bill_id = %s ORDER BY line_number""",
                (bill["stg_bill_id"],),
            )
            lines = cur.fetchall()
        subtotal = bill["subtotal"] if bill["subtotal"] is not None else bill["total_amount"]
        result = contract_overbilling(lines, rates, subtotal)
        if result is None:
            return None
        currency = (bill["currency_code"] or rates[0]["currency_code"] or "USD").strip()
        finding = {
            "code": "OVERBILLED_VS_CONTRACT", "severity": "HIGH",
            "title": f"Invoice {bill['invoice_number'] or ''} is {currency} {result['over_by']} above the contract rate".replace("  ", " "),
            "details": {**result, "currency": currency, "money_at_risk": result["over_by"],
                        "billed": str(subtotal), "invoice_number": bill["invoice_number"]},
        }
        raise_finding(cur, tenant_id, finding["code"], finding["severity"], "bill", invoice_id,
                      finding["title"], finding["details"])
        return finding


def wallet_addresses_in(text: str) -> set[str]:
    return {a.lower() for a in ADDRESS_RE.findall(text or "")}


def check_invoice_wallet(cur, tenant_id: str, invoice_id: str) -> Optional[dict]:
    """Flag a wallet printed on the invoice that is not the vendor's verified wallet."""
    cur.execute(
        """SELECT fi.invoice_id::text AS invoice_id, fi.supplier_id::text AS supplier_id, fi.invoice_number,
                  fi.upload_id, COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) AS stg_bill_id
             FROM fact_invoices fi WHERE fi.invoice_id = %s AND fi.tenant_id = %s""",
        (invoice_id, tenant_id),
    )
    bill = cur.fetchone()
    if not bill:
        return None
    texts = []
    if bill["upload_id"]:
        cur.execute("SELECT raw_response_jsonb::text AS t FROM raw_ocr_results WHERE upload_id = %s AND tenant_id = %s",
                    (bill["upload_id"], tenant_id))
        texts += [r["t"] for r in cur.fetchall()]
    if bill["stg_bill_id"]:
        cur.execute("SELECT string_agg(coalesce(product_name, ''), ' ') AS t FROM stg_bill_line_items WHERE stg_bill_id = %s",
                    (bill["stg_bill_id"],))
        row = cur.fetchone()
        texts.append(row["t"] if row else "")
    found = wallet_addresses_in(" ".join(t for t in texts if t))
    if not found:
        return None
    settings = get_settings()
    cur.execute("SELECT lower(address) AS a FROM treasury_wallets WHERE tenant_id = %s", (tenant_id,))
    ignore = {r["a"] for r in cur.fetchall()} | {settings.usdc_address.lower(),
                                                 settings.multisend_call_only_address.lower()}
    found -= ignore
    if not found:
        return None
    active = set()
    if bill["supplier_id"]:
        cur.execute(
            """SELECT lower(address) AS a FROM supplier_wallets
                WHERE tenant_id = %s AND supplier_id = %s AND status = 'ACTIVE'""",
            (tenant_id, bill["supplier_id"]),
        )
        active = {r["a"] for r in cur.fetchall()}
    strangers = sorted(found - active)
    if not strangers:
        return None
    finding = {
        "code": "WALLET_MISMATCH", "severity": "HIGH",
        "title": "Invoice shows a wallet that is not the vendor's verified wallet",
        "details": {"invoice_number": bill["invoice_number"], "invoice_addresses": strangers,
                    "verified_addresses": sorted(active),
                    "detail": "Onsumm pays only the verified wallet. Confirm with the vendor through a known contact."},
    }
    raise_finding(cur, tenant_id, finding["code"], finding["severity"], "bill", invoice_id,
                  finding["title"], finding["details"])
    return finding


def run_bill_checks(conn, tenant_id: str, invoice_id: str) -> list[dict]:
    """Both pre-payment checks for one bill; files findings and returns them."""
    findings = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for check in (lambda: check_invoice_wallet(cur, tenant_id, invoice_id),
                      lambda: ContractRateCheck().run(cur, tenant_id, invoice_id)):
            f = check()
            if f:
                findings.append(f)
        conn.commit()
    return findings
