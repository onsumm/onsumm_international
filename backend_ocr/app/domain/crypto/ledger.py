"""Books for confirmed USDC payouts: a Tally payment voucher per payout.

Until now Onsumm never posted a paid bill back to any ledger. A confirmed USDC
payout now produces one payment voucher:

    Dr  <vendor ledger>                 gross bill amount paid
    Cr  USDC Wallet                     net amount the vendor received
    Cr  Crypto Withholding Payable      tax withheld (only when > 0)
    Dr  Crypto Network Fees             network fee (only when the client paid it)

Amounts are in the tenant's functional currency (USD for the demo tenant).
With Safe custody the gas-only relayer pays the network fee, not the client,
so that line is normally absent; it appears when a fee in functional currency
is recorded for the payout (e.g. a partner custody model that charges it).

The narration carries the transaction hash and explorer link, which is the
payment reference an auditor follows.

How it reaches Tally
────────────────────
`enqueue_payout_posting` is called inside the payout's success transaction and
only inserts a `ledger_postings` row (status PENDING). `LedgerPoster` turns
PENDING rows into Tally import XML and marks them READY. Delivery to a live
Tally follows the existing desktop-agent route (routers/tally_agent.py): the
agent paired to the tenant polls for READY postings and imports the XML into
Tally Prime, then the row becomes POSTED with Tally's voucher number in
`external_ref`. That pull endpoint is outside this module. Nothing here can
fail the payout: errors are recorded on the posting row and retried.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from xml.sax.saxutils import escape

import psycopg2.extras

logger = logging.getLogger(__name__)

USDC_LEDGER = "USDC Wallet"
WITHHOLDING_LEDGER = "Crypto Withholding Payable"
FEE_LEDGER = "Crypto Network Fees"
MAX_ATTEMPTS = 5


def _x(value) -> str:
    return "" if value is None else escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _money(value) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _tally_date(value) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")[:8]


class TallyPaymentVoucherBuilder:
    """Builds a Tally Prime import ENVELOPE holding one Payment voucher.

    Tally convention (same as tally2database/tally_builder.py): a debit line is
    ISDEEMEDPOSITIVE=Yes with a negative AMOUNT; a credit line is No and positive.
    """

    def build(self, voucher: dict) -> str:
        lines = []
        for line in voucher["lines"]:
            debit = line["side"] == "Dr"
            amount = _money(line["amount"])
            lines.append(f"""
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>{_x(line['ledger'])}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>{'Yes' if debit else 'No'}</ISDEEMEDPOSITIVE>
            <AMOUNT>{'-' if debit else ''}{amount}</AMOUNT>
          </ALLLEDGERENTRIES.LIST>""")
        return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Payment" ACTION="Create">
            <DATE>{_tally_date(voucher['date'])}</DATE>
            <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{_x(voucher['voucher_number'])}</VOUCHERNUMBER>
            <PARTYLEDGERNAME>{_x(voucher['party'])}</PARTYLEDGERNAME>
            <NARRATION>{_x(voucher['narration'])}</NARRATION>{''.join(lines)}
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>
"""


def voucher_lines(gross, net, withholding, fee=None) -> list[dict]:
    """Debits equal credits by construction: gross = net + withholding."""
    lines = [{"ledger": None, "side": "Dr", "amount": _money(gross)},
             {"ledger": USDC_LEDGER, "side": "Cr", "amount": _money(net)}]
    if Decimal(str(withholding or 0)) > 0:
        lines.append({"ledger": WITHHOLDING_LEDGER, "side": "Cr", "amount": _money(withholding)})
    if fee is not None and Decimal(str(fee)) > 0:
        lines.append({"ledger": FEE_LEDGER, "side": "Dr", "amount": _money(fee)})
        lines.append({"ledger": USDC_LEDGER, "side": "Cr", "amount": _money(fee)})
    return lines


def enqueue_payout_posting(cur, tenant_id: str, disbursement_id: str) -> Optional[str]:
    """Queue the payment voucher for one confirmed payout. Idempotent per payout.
    Called from the payout's success transaction; never raises into it."""
    try:
        cur.execute("SAVEPOINT ledger_enqueue")
        cur.execute(
            """SELECT id FROM ledger_postings
                WHERE tenant_id = %s AND source_type = 'CRYPTO_PAYOUT' AND source_id = %s AND target_system = 'TALLY'""",
            (tenant_id, str(disbursement_id)),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute("RELEASE SAVEPOINT ledger_enqueue")
            return str(existing["id"] if isinstance(existing, dict) else existing[0])

        cur.execute(
            """SELECT d.disbursement_id::text AS disbursement_id, d.invoice_id::text AS invoice_id,
                      d.amount, d.currency_code, d.withholding_amount, d.net_amount, d.beneficiary_name,
                      fi.invoice_number, ds.name AS supplier_name, t.base_currency,
                      cdd.tx_hash, cdd.chain, cdd.amount_units, cdd.confirmed_at
                 FROM fact_disbursements d
                 JOIN crypto_disbursement_details cdd ON cdd.disbursement_id = d.disbursement_id
                 LEFT JOIN fact_invoices fi ON fi.invoice_id = d.invoice_id
                 LEFT JOIN dim_suppliers ds ON ds.supplier_id = d.supplier_id
                 LEFT JOIN dim_tenants t ON t.tenant_id = d.tenant_id
                WHERE d.disbursement_id = %s AND d.tenant_id = %s""",
            (disbursement_id, tenant_id),
        )
        p = cur.fetchone()
        if p is None:
            cur.execute("RELEASE SAVEPOINT ledger_enqueue")
            return None
        p = dict(p) if isinstance(p, dict) else dict(zip([c.name for c in cur.description], p))
        from ...integrations.chains.evm import explorer_tx_url
        gross = Decimal(str(p["amount"] or 0))
        withholding = Decimal(str(p["withholding_amount"] or 0))
        net = Decimal(str(p["net_amount"])) if p["net_amount"] is not None else gross - withholding
        party = p["supplier_name"] or p["beneficiary_name"] or "Vendor"
        lines = voucher_lines(gross, net, withholding)
        lines[0]["ledger"] = party
        explorer = explorer_tx_url(p["tx_hash"]) if p["tx_hash"] else ""
        payload = {
            "date": (p["confirmed_at"] or datetime.now(timezone.utc)).date().isoformat(),
            "voucher_number": f"USDC-{(p['invoice_number'] or p['disbursement_id'][:8])}",
            "party": party,
            "currency": (p["base_currency"] or p["currency_code"] or "USD").strip(),
            "narration": f"USDC payout for {p['invoice_number'] or 'bill'} on {p['chain']}. Tx {p['tx_hash']} {explorer}",
            "tx_hash": p["tx_hash"],
            "explorer": explorer,
            "lines": lines,
        }
        cur.execute(
            """INSERT INTO ledger_postings (tenant_id, source_type, source_id, target_system, payload, status)
               VALUES (%s, 'CRYPTO_PAYOUT', %s, 'TALLY', %s::jsonb, 'PENDING') RETURNING id""",
            (tenant_id, str(disbursement_id), json.dumps(payload, default=str)),
        )
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT ledger_enqueue")
        return str(row["id"] if isinstance(row, dict) else row[0])
    except Exception:
        logger.exception("[ledger] could not queue the voucher for payout %s", disbursement_id)
        try:
            cur.execute("ROLLBACK TO SAVEPOINT ledger_enqueue")
        except Exception:
            pass
        return None


class LedgerPoster:
    """PENDING → READY: renders the Tally XML for queued postings."""

    def __init__(self, builder: Optional[TallyPaymentVoucherBuilder] = None):
        self.builder = builder or TallyPaymentVoucherBuilder()

    def process_pending(self, conn, limit: int = 50) -> int:
        done = 0
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, payload FROM ledger_postings
                    WHERE status IN ('PENDING', 'ERROR') AND target_system = 'TALLY'
                      AND COALESCE(attempts, 0) < %s
                    ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED""",
                (MAX_ATTEMPTS, limit),
            )
            for row in cur.fetchall():
                payload = dict(row["payload"] or {})
                try:
                    payload["xml"] = self.builder.build(payload)
                    cur.execute(
                        "UPDATE ledger_postings SET payload = %s::jsonb, status = 'READY', error = NULL, updated_at = now() WHERE id = %s",
                        (json.dumps(payload, default=str), row["id"]),
                    )
                    done += 1
                except Exception as e:
                    cur.execute(
                        """UPDATE ledger_postings SET status = 'ERROR', error = %s,
                                  attempts = COALESCE(attempts, 0) + 1, updated_at = now() WHERE id = %s""",
                        (str(e)[:500], row["id"]),
                    )
            conn.commit()
        return done


def voucher_xml(cur, tenant_id: str, posting_id: str) -> Optional[str]:
    """XML for one posting, rendering it on the fly if the poster hasn't yet."""
    cur.execute("SELECT payload FROM ledger_postings WHERE id = %s AND tenant_id = %s", (posting_id, tenant_id))
    row = cur.fetchone()
    if not row:
        return None
    payload = row["payload"] if isinstance(row, dict) else row[0]
    return (payload or {}).get("xml") or TallyPaymentVoucherBuilder().build(payload)
