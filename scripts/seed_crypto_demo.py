#!/usr/bin/env python3
"""Seed (or reset) the crypto rail hackathon demo. Run from the repository root:

    python3.12 scripts/seed_crypto_demo.py            # create or refresh the demo
    python3.12 scripts/seed_crypto_demo.py --reset    # wipe the demo tenants' data first

What it creates (docs/crypto-rail/00_DECISIONS.md §2.6, the demo cast):

  * Lumen Labs Pte Ltd — Singapore, books in USD, KYB approved, crypto enabled,
    rail policy for the demo (2-minute cooling, no test transfer), the SG-DEMO
    withholding pack (0%, adviser sign-off required).
  * Onsumm Demo India Pvt Ltd — India-resident; crypto stays disabled. Used for
    the guardrail moment (CRYPTO_DISABLED_RESIDENT_INDIA).
  * Users: lumen.asha (admin), lumen.ben (finance_controller), lumen.chen
    (approver), india.priya (admin). Password: DEMO_PASSWORD or Demo@2026!.
  * Vendor Nova Design LLC (US) in both tenants, W-8BEN-E on file, with a
    USD 3,600 contract rate for "Brand design retainer - October 2026".
  * Bills, each with a generated PDF and OCR text like a real upload:
      NOVA-1042  USD 4,200  overbilled by 600, and its PDF says "pay to" a wallet
                            that is not Nova's verified one (WALLET_MISMATCH)
      NOVA-1043  USD 1,200  clean — the fallback bill for the demo
      NOVA-IN-77 USD 1,000  in the India tenant, for the guardrail moment
  * Treasury wallets, only when SAFE_ADDRESS (Lumen's Safe) and optionally
    TAX_WALLET_ADDRESS are set and the chain is reachable: owners and threshold
    are read from the chain, never typed in.
  * Nova's wallet is NOT pre-registered: the demo registers it live through the
    vendor portal. Set NOVA_WALLET_ADDRESS and pass --preapprove-wallet to
    pre-approve it as a fallback.

Every row uses fixed demo UUIDs, so re-running updates in place and --reset only
ever touches the demo tenants. Nothing here emails anyone: Nova's email is left
empty unless DEMO_VENDOR_EMAIL is set.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

import bcrypt  # noqa: E402
import psycopg2.extras  # noqa: E402

from backend_ocr.app.config import get_conn, put_conn  # noqa: E402

LUMEN = "c0de0000-0000-4000-8000-00000000a001"
INDIA = "c0de0000-0000-4000-8000-00000000a002"
NOVA_LUMEN = "c0de0000-0000-4000-8000-00000000b001"
NOVA_INDIA = "c0de0000-0000-4000-8000-00000000b002"
TEMPLATE_TENANT = "d0e00000-0000-0000-0000-000000000001"  # licences are copied from the main demo tenant

# The wallet printed on NOVA-1042's PDF. It is deliberately NOT Nova's verified
# wallet: the demo shows Onsumm refusing to pay an address read off an invoice.
INVOICE_WALLET = "0x5eE7b1C4aF0e9C2fA1d3B6c8E0a9F4d2b7C1e3A5"
CONTRACT_ITEM = "Brand design retainer - October 2026"

BILLS = [
    # (tenant, supplier, bill uuid, stg uuid, upload uuid, number, amount, wallet on invoice)
    (LUMEN, NOVA_LUMEN, "c0de0000-0000-4000-8000-00000000c001", "c0de0000-0000-4000-8000-00000000d001",
     "c0de0000-0000-4000-8000-00000000e001", "NOVA-1042", "4200.00", INVOICE_WALLET),
    (LUMEN, NOVA_LUMEN, "c0de0000-0000-4000-8000-00000000c002", "c0de0000-0000-4000-8000-00000000d002",
     "c0de0000-0000-4000-8000-00000000e002", "NOVA-1043", "1200.00", None),
    # Small enough to pay in full from a faucet-funded Safe (20 test USDC), so the
    # demo can show a bill going all the way to "paid".
    (LUMEN, NOVA_LUMEN, "c0de0000-0000-4000-8000-00000000c004", "c0de0000-0000-4000-8000-00000000d004",
     "c0de0000-0000-4000-8000-00000000e004", "NOVA-1044", "15.00", None),
    (INDIA, NOVA_INDIA, "c0de0000-0000-4000-8000-00000000c003", "c0de0000-0000-4000-8000-00000000d003",
     "c0de0000-0000-4000-8000-00000000e003", "NOVA-IN-77", "1000.00", None),
]

CRYPTO_SERVICES = ("disbursement", "crypto_payouts", "crypto_tds", "audit_anchor")

# Tables holding per-tenant crypto rail rows, cleared by --reset (child tables first).
RESET_TABLES = (
    "crypto_withholdings", "crypto_disbursement_details",
    "withholding_remittances", "chain_transfers", "ledger_postings", "supplier_wallets",
    "vendor_portal_nonces", "vendor_contract_rates", "tenant_ubos", "tenant_rail_policy",
    "treasury_wallets", "fact_disbursements", "disbursement_gateway_calls",
)


def _invoice_pdf(number: str, amount: str, wallet: str | None) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 20)
    c.drawString(50, h - 70, "Nova Design LLC")
    c.setFont("Helvetica", 10)
    c.drawString(50, h - 88, "500 Market Street, San Francisco, CA 94105, United States · EIN 88-1234567")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, h - 130, f"INVOICE {number}")
    c.setFont("Helvetica", 11)
    c.drawString(50, h - 150, "Date: 26 Oct 2026      Due: 9 Nov 2026      Currency: USD")
    c.drawString(50, h - 180, "Bill to: Lumen Labs Pte Ltd, 1 Raffles Place, Singapore 048616")
    c.line(50, h - 200, w - 50, h - 200)
    c.drawString(50, h - 218, "Description")
    c.drawRightString(w - 50, h - 218, "Amount (USD)")
    c.line(50, h - 226, w - 50, h - 226)
    c.drawString(50, h - 246, CONTRACT_ITEM)
    c.drawRightString(w - 50, h - 246, f"{float(amount):,.2f}")
    c.line(50, h - 262, w - 50, h - 262)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, h - 282, "Total due")
    c.drawRightString(w - 50, h - 282, f"USD {float(amount):,.2f}")
    c.setFont("Helvetica", 10)
    if wallet:
        c.drawString(50, h - 330, "Payment: USDC on Base. Pay to wallet:")
        c.setFont("Courier", 10)
        c.drawString(50, h - 346, wallet)
    else:
        c.drawString(50, h - 330, "Payment: USDC to the wallet registered with your Onsumm vendor portal.")
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 40, "Demo invoice generated for Onsumm Crypto Rail · Hacker House Goa 2026")
    c.save()
    return buf.getvalue()


def _ocr_text(number: str, amount: str, wallet: str | None) -> str:
    lines = [
        "Nova Design LLC", f"INVOICE {number}", "Date: 26 Oct 2026", "Currency: USD",
        "Bill to: Lumen Labs Pte Ltd", f"{CONTRACT_ITEM} {amount}", f"Total due USD {amount}",
    ]
    if wallet:
        lines.append(f"Payment: USDC on Base. Pay to wallet: {wallet}")
    return "\n".join(lines)


def reset(cur) -> None:
    tenants = [LUMEN, INDIA]
    # Signatures carry no tenant_id of their own; they hang off a payout.
    cur.execute("SELECT to_regclass('crypto_payout_signatures') IS NOT NULL AS ok")
    if cur.fetchone()["ok"]:
        cur.execute("""DELETE FROM crypto_payout_signatures WHERE disbursement_id IN
                         (SELECT disbursement_id FROM fact_disbursements WHERE tenant_id = ANY(%s::uuid[]))""",
                    (tenants,))
    for table in RESET_TABLES:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (table,))
        if cur.fetchone()["ok"]:
            cur.execute(f"DELETE FROM {table} WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    cur.execute("DELETE FROM bill_approval_chain WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    cur.execute("DELETE FROM fact_invoices WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    cur.execute("DELETE FROM stg_bill_line_items WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    cur.execute("DELETE FROM stg_bills WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    cur.execute("DELETE FROM raw_ocr_results WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    cur.execute("DELETE FROM raw_invoice_uploads WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    # Review Hub findings filed against the demo tenants by earlier runs.
    cur.execute("SELECT to_regclass('rh_findings') IS NOT NULL AS ok")
    if cur.fetchone()["ok"]:
        cur.execute("""DELETE FROM rh_finding_events WHERE finding_id IN
                         (SELECT finding_id FROM rh_findings WHERE tenant_id = ANY(%s::uuid[]))""", (tenants,))
        cur.execute("""DELETE FROM rh_finding_entities WHERE finding_id IN
                         (SELECT finding_id FROM rh_findings WHERE tenant_id = ANY(%s::uuid[]))""", (tenants,))
        cur.execute("DELETE FROM rh_findings WHERE tenant_id = ANY(%s::uuid[])", (tenants,))
    print("[seed] reset: demo tenants' crypto rail, bill and finding rows removed")


def seed_tenants(cur) -> None:
    lumen_settings = {
        "crypto_withholding": {
            "pack": "SG-DEMO",
            "default": {"code": "SG-DEMO-SERVICES-ABROAD", "rate": "0", "rate_no_tax_id": "0",
                        "requires_signoff": True,
                        "note": "Demo configuration: services performed outside Singapore. "
                                "To be confirmed by Lumen's tax adviser."},
        },
    }
    rows = [
        (LUMEN, "Lumen Labs Pte Ltd", "Lumen Labs Pte Ltd", "SG", "USD", "international", False, "APPROVED",
         "202312345K", "SG", "Asia/Singapore", "ap-south-1", json.dumps(lumen_settings)),
        (INDIA, "Onsumm Demo India Pvt Ltd", "Onsumm Demo India Private Limited", "IN", "INR", "india", True,
         "APPROVED", "U72900KA2026PTC000001", "IN", "Asia/Kolkata", "ap-south-1", json.dumps({})),
    ]
    for r in rows:
        cur.execute(
            """INSERT INTO dim_tenants (tenant_id, company_name, legal_name, country_code, base_currency,
                                        jurisdiction_profile, is_resident_india, kyb_status, registration_number,
                                        tax_residency, timezone, region, settings_jsonb, plan_tier, onboarded_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'pro',now())
               ON CONFLICT (tenant_id) DO UPDATE SET
                 company_name=EXCLUDED.company_name, legal_name=EXCLUDED.legal_name,
                 country_code=EXCLUDED.country_code, base_currency=EXCLUDED.base_currency,
                 jurisdiction_profile=EXCLUDED.jurisdiction_profile, is_resident_india=EXCLUDED.is_resident_india,
                 kyb_status=EXCLUDED.kyb_status, registration_number=EXCLUDED.registration_number,
                 tax_residency=EXCLUDED.tax_residency, timezone=EXCLUDED.timezone,
                 settings_jsonb=dim_tenants.settings_jsonb || EXCLUDED.settings_jsonb""",
            r,
        )
        # Licences: everything the main demo tenant has, plus the crypto rail.
        cur.execute(
            """INSERT INTO tenant_service (tenant_id, service_key, enabled, enabled_at)
               SELECT %s, service_key, enabled, now() FROM tenant_service WHERE tenant_id = %s
               ON CONFLICT (tenant_id, service_key) DO UPDATE SET enabled = EXCLUDED.enabled""",
            (r[0], TEMPLATE_TENANT),
        )
        for key in CRYPTO_SERVICES:
            cur.execute(
                """INSERT INTO tenant_service (tenant_id, service_key, enabled, enabled_at)
                   VALUES (%s,%s,true,now())
                   ON CONFLICT (tenant_id, service_key) DO UPDATE SET enabled = true, disabled_at = NULL""",
                (r[0], key),
            )
        # demo_payments would short-circuit the IMPS gateway; it plays no part in USDC payouts.
        cur.execute("UPDATE tenant_service SET enabled=false WHERE tenant_id=%s AND service_key='demo_payments'",
                    (r[0],))

    cur.execute(
        """INSERT INTO tenant_rail_policy (tenant_id, crypto_enabled, allowed_chains, allowed_tokens, per_tx_limit_usd,
                                           daily_limit_usd, dual_control_above_usd, cooling_minutes,
                                           require_test_transfer)
           VALUES (%s, true, ARRAY['base-sepolia'], ARRAY['USDC'], 10000, 25000, 10000, 2, false),
                  (%s, false, ARRAY['base-sepolia'], ARRAY['USDC'], 10000, 25000, 10000, 1440, true)
           ON CONFLICT (tenant_id) DO UPDATE SET
             crypto_enabled=EXCLUDED.crypto_enabled, allowed_chains=EXCLUDED.allowed_chains,
             allowed_tokens=EXCLUDED.allowed_tokens, per_tx_limit_usd=EXCLUDED.per_tx_limit_usd,
             daily_limit_usd=EXCLUDED.daily_limit_usd, dual_control_above_usd=EXCLUDED.dual_control_above_usd,
             cooling_minutes=EXCLUDED.cooling_minutes, require_test_transfer=EXCLUDED.require_test_transfer,
             updated_at=now()""",
        (LUMEN, INDIA),
    )
    cur.execute("DELETE FROM tenant_ubos WHERE tenant_id = %s", (LUMEN,))
    for name, country, pct in (("Asha Raman", "SG", 60), ("Northwind Ventures Fund II", "US", 30)):
        cur.execute(
            """INSERT INTO tenant_ubos (tenant_id, name, country_code, ownership_pct, screening_status, screened_at)
               VALUES (%s,%s,%s,%s,'CLEAR',now())""",
            (LUMEN, name, country, pct),
        )
    print("[seed] tenants: Lumen Labs Pte Ltd (SG, USD, crypto on), Onsumm Demo India Pvt Ltd (IN, crypto off)")


def seed_users(cur) -> None:
    password = os.getenv("DEMO_PASSWORD", "Demo@2026!")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users = [
        ("lumen.asha", "admin", "Asha Raman (Founder)", LUMEN),
        ("lumen.ben", "finance_controller", "Ben Ortiz (Finance controller)", LUMEN),
        ("lumen.chen", "approver", "Chen Wei (CFO backup)", LUMEN),
        ("india.priya", "admin", "Priya Nair (Admin)", INDIA),
    ]
    for username, role, name, tenant in users:
        cur.execute(
            """INSERT INTO users (username, password_hash, role, name, email, company_id)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (username) DO UPDATE SET password_hash=EXCLUDED.password_hash, role=EXCLUDED.role,
                 name=EXCLUDED.name, company_id=EXCLUDED.company_id""",
            (username, hashed, role, name, None, tenant),
        )
    print(f"[seed] users: lumen.asha, lumen.ben, lumen.chen, india.priya — password {password!r}")


def seed_vendors(cur) -> None:
    email = os.getenv("DEMO_VENDOR_EMAIL") or None
    for supplier_id, tenant in ((NOVA_LUMEN, LUMEN), (NOVA_INDIA, INDIA)):
        cur.execute(
            """INSERT INTO dim_suppliers (supplier_id, tenant_id, source_supplier_id, source, name, legal_name, email,
                                          status, country_code, tax_residency, preferred_rail, tax_docs, category)
               VALUES (%s,%s,'NOVA-DESIGN-LLC','MANUAL','Nova Design LLC','Nova Design LLC',%s,'UNVERIFIED',
                       'US','US','CRYPTO',%s::jsonb,'Design services')
               ON CONFLICT (supplier_id) DO UPDATE SET email=EXCLUDED.email, country_code='US',
                 tax_residency='US', preferred_rail='CRYPTO', tax_docs=EXCLUDED.tax_docs""",
            (supplier_id, tenant, email,
             json.dumps({"W-8BEN-E": "W8-NOVA-2026 (on file, demo)", "tax_id": "EIN 88-1234567"})),
        )
        cur.execute("DELETE FROM vendor_contract_rates WHERE tenant_id=%s AND supplier_id=%s", (tenant, supplier_id))
        cur.execute(
            """INSERT INTO vendor_contract_rates (tenant_id, supplier_id, item_key, unit_price, currency_code,
                                                  valid_from, valid_to)
               VALUES (%s,%s,%s,3600,'USD','2026-01-01','2026-12-31')""",
            (tenant, supplier_id, CONTRACT_ITEM),
        )
    print(f"[seed] vendor: Nova Design LLC (US, W-8BEN-E), contract rate USD 3,600 for {CONTRACT_ITEM!r}")


# Line item per bill; anything not listed bills the contracted retainer.
LINE_ITEMS = {
    "NOVA-1043": "Website illustrations - 4 assets",
    "NOVA-1044": "Icon set - 3 icons",
}


def seed_bills(cur, only: set[str] | None = None) -> None:
    today = date(2026, 10, 26)
    now = datetime.now(timezone.utc)
    for tenant, supplier, invoice_id, stg_id, upload_id, number, amount, wallet in BILLS:
        if only and number not in only:
            continue
        pdf = _invoice_pdf(number, amount, wallet)
        cur.execute(
            """INSERT INTO raw_invoice_uploads (upload_id, tenant_id, file_name, file_type, file_size_bytes,
                                                storage_path, status, ocr_engine_used, file_data,
                                                uploaded_by_name, uploaded_by_role)
               VALUES (%s,%s,%s,'PDF',%s,%s,'DONE','MERGED',%s,'Demo seed','admin')
               ON CONFLICT (upload_id) DO UPDATE SET file_data=EXCLUDED.file_data,
                 file_size_bytes=EXCLUDED.file_size_bytes""",
            (upload_id, tenant, f"{number}.pdf", len(pdf), f"db://{upload_id}", psycopg2.Binary(pdf)),
        )
        cur.execute("DELETE FROM raw_ocr_results WHERE upload_id=%s", (upload_id,))
        cur.execute(
            """INSERT INTO raw_ocr_results (upload_id, tenant_id, engine, page_number, raw_response_jsonb)
               VALUES (%s,%s,'MERGED',1,%s::jsonb)""",
            (upload_id, tenant, json.dumps({"text": _ocr_text(number, amount, wallet), "source": "demo seed"})),
        )
        cur.execute(
            """INSERT INTO stg_bills (stg_bill_id, tenant_id, source, source_bill_id, source_updated_at, bill_number,
                                      bill_date, due_date, supplier_id, currency_code, subtotal, tax_amount,
                                      total_amount, amount_paid, outstanding_amount, status, upload_id, category,
                                      original_amount)
               VALUES (%s,%s,'UPLOAD',%s,%s,%s,%s,%s,%s,'USD',%s,0,%s,0,%s,'APPROVED',%s,'Design services',%s)
               ON CONFLICT (stg_bill_id) DO UPDATE SET total_amount=EXCLUDED.total_amount,
                 subtotal=EXCLUDED.subtotal, outstanding_amount=EXCLUDED.outstanding_amount, amount_paid=0""",
            (stg_id, tenant, f"demo-{number}", now, number, today, date(2026, 11, 9), supplier,
             amount, amount, amount, upload_id, amount),
        )
        cur.execute("DELETE FROM stg_bill_line_items WHERE stg_bill_id=%s", (stg_id,))
        cur.execute(
            """INSERT INTO stg_bill_line_items (stg_bill_id, tenant_id, line_number, product_name, quantity, unit,
                                                unit_price, tax_rate_percent, tax_amount, line_total)
               VALUES (%s,%s,1,%s,1,'month',%s,0,0,%s)""",
            (stg_id, tenant, LINE_ITEMS.get(number, CONTRACT_ITEM),
             amount, amount),
        )
        cur.execute(
            """INSERT INTO fact_invoices (invoice_id, tenant_id, stg_bill_id, supplier_id, upload_id, invoice_number,
                                          invoice_date, due_date, fiscal_year, fiscal_quarter, fiscal_month,
                                          currency_code, subtotal, tax_amount, total_amount, amount_paid,
                                          outstanding_amount, status, source, source_updated_at, invoice_type,
                                          payment_status, category, original_amount)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,2027,3,7,'USD',%s,0,%s,0,%s,'APPROVED','UPLOAD',%s,'AP',
                       'unpaid','Design services',%s)
               ON CONFLICT (invoice_id) DO UPDATE SET total_amount=EXCLUDED.total_amount,
                 subtotal=EXCLUDED.subtotal, outstanding_amount=EXCLUDED.outstanding_amount, amount_paid=0,
                 payment_status='unpaid', status='APPROVED'""",
            (invoice_id, tenant, stg_id, supplier, upload_id, number, today, date(2026, 11, 9),
             amount, amount, amount, now, amount),
        )
        cur.execute("DELETE FROM bill_approval_chain WHERE invoice_id=%s", (invoice_id,))
        cur.execute(
            """INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment)
               VALUES (%s,%s,1,'Approver','Demo seed','approve','Seeded as approved for the demo')""",
            (invoice_id, tenant),
        )
    if only:
        print(f"[seed] bills: {', '.join(sorted(only))}")
        return
    print("[seed] bills: NOVA-1042 (USD 4,200, overbilled, wrong wallet on PDF), NOVA-1043 (USD 1,200), "
          "NOVA-1044 (USD 15, payable in full from a faucet-funded Safe), "
          "NOVA-IN-77 (USD 1,000, India tenant)")


def seed_wallets(cur, preapprove: bool) -> None:
    safe = os.getenv("SAFE_ADDRESS", "").strip()
    tax = os.getenv("TAX_WALLET_ADDRESS", "").strip()
    if not safe:
        print("[seed] treasury: SAFE_ADDRESS not set — connect Lumen's Safe in Crypto Setup instead")
    else:
        try:
            from web3 import Web3
            from backend_ocr.app.integrations.chains.evm import get_chain
            from backend_ocr.app.integrations.custody.safe import get_safe_custody

            info = get_safe_custody(get_chain()).info(safe)
            cur.execute(
                """INSERT INTO treasury_wallets (tenant_id, role, chain, address, custody, safe_threshold,
                                                 safe_owners, status)
                   VALUES (%s,'TREASURY','base-sepolia',%s,'SAFE',%s,%s,'ACTIVE')
                   ON CONFLICT (tenant_id, chain, address) DO UPDATE SET safe_threshold=EXCLUDED.safe_threshold,
                     safe_owners=EXCLUDED.safe_owners, status='ACTIVE', updated_at=now()""",
                (LUMEN, info.address, info.threshold, list(info.owners)),
            )
            print(f"[seed] treasury: Safe {info.address} — {info.threshold}-of-{len(info.owners)} read on-chain")
            if tax:
                cur.execute(
                    """INSERT INTO treasury_wallets (tenant_id, role, chain, address, custody, status)
                       VALUES (%s,'TAX','base-sepolia',%s,'EOA','ACTIVE')
                       ON CONFLICT (tenant_id, chain, address) DO NOTHING""",
                    (LUMEN, Web3.to_checksum_address(tax)),
                )
                print(f"[seed] tax wallet: {Web3.to_checksum_address(tax)}")
        except Exception as e:  # chain not configured or not a Safe
            print(f"[seed] treasury: could not read Safe {safe} on-chain ({e}); connect it in Crypto Setup")

    nova = os.getenv("NOVA_WALLET_ADDRESS", "").strip()
    if preapprove and nova:
        from web3 import Web3
        cur.execute("DELETE FROM supplier_wallets WHERE tenant_id=%s AND supplier_id=%s", (LUMEN, NOVA_LUMEN))
        cur.execute(
            """INSERT INTO supplier_wallets (tenant_id, supplier_id, chain, asset, address, is_primary, verified_at,
                                             cooling_until, screening_status, status, approved_by)
               VALUES (%s,%s,'base-sepolia','USDC',%s,true,now(),now(),'CLEAR','ACTIVE','demo seed (fallback)')""",
            (LUMEN, NOVA_LUMEN, Web3.to_checksum_address(nova)),
        )
        print(f"[seed] Nova wallet pre-approved as a fallback: {Web3.to_checksum_address(nova)}")
    else:
        print("[seed] Nova wallet: not pre-registered — register it live through the vendor portal")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reset", action="store_true", help="wipe the demo tenants' crypto rail, bill and finding rows first")
    ap.add_argument("--bills", nargs="+", metavar="NUMBER",
                    help="only (re)create these bills, e.g. --bills NOVA-1044; nothing else is touched")
    ap.add_argument("--preapprove-wallet", action="store_true",
                    help="register NOVA_WALLET_ADDRESS as Nova's ACTIVE wallet (demo fallback)")
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.bills:
                seed_bills(cur, set(args.bills))
                conn.commit()
                print(f"[seed] done: {', '.join(args.bills)} only.")
                return
            if args.reset:
                reset(cur)
            seed_tenants(cur)
            seed_users(cur)
            seed_vendors(cur)
            seed_bills(cur)
            seed_wallets(cur, args.preapprove_wallet)
        conn.commit()
        print("[seed] done. Sign in as lumen.asha to start the demo.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


if __name__ == "__main__":
    main()
