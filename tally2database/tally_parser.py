"""
tally_parser.py — Tally XML → Python dicts matching Onsum schema
"""
import re
import logging
from datetime import date
from lxml import etree

logger = logging.getLogger(__name__)

_INVALID_XML_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]",
    re.UNICODE,
)

def _clean_xml(xml_text: str) -> bytes:
    # remove raw invalid chars
    xml_text = _INVALID_XML_CHARS.sub("", xml_text)

    # 🔥 remove invalid numeric character references like &#x4; or &#4;
    xml_text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: "" if int(m.group(1), 16) < 32 else m.group(0), xml_text)
    xml_text = re.sub(r"&#([0-9]+);", lambda m: "" if int(m.group(1)) < 32 else m.group(0), xml_text)

    return xml_text.encode("utf-8", errors="replace")


def _text(el, tag, default=""):
    node = el.find(tag)
    return node.text.strip() if node is not None and node.text else default


def _date(tally_date):
    if tally_date and len(tally_date) == 8:
        try:
            return date(int(tally_date[:4]), int(tally_date[4:6]), int(tally_date[6:]))
        except ValueError:
            pass
    return None


def _amount(raw):
    raw = raw.strip().replace(",", "")
    if raw.endswith(" Cr"):
        return float(raw[:-3])
    if raw.endswith(" Dr"):
        return -float(raw[:-3])
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _map_account_type(tally_group):
    mapping = {
        "Sundry Debtors": "ASSET", "Sundry Creditors": "LIABILITY",
        "Bank Accounts": "BANK", "Cash-in-Hand": "CASH",
        "Capital Account": "EQUITY", "Sales Accounts": "REVENUE",
        "Purchase Accounts": "EXPENSE", "Direct Expenses": "EXPENSE",
        "Indirect Expenses": "EXPENSE", "Direct Incomes": "REVENUE",
        "Indirect Incomes": "REVENUE", "Fixed Assets": "ASSET",
        "Current Assets": "ASSET", "Current Liabilities": "LIABILITY",
    }
    return mapping.get(tally_group, "ASSET")


def parse_ledgers(xml_text):
    results = []
    try:
        parser = etree.XMLParser(recover=True, encoding="utf-8")  # ✅ FIX
        root = etree.fromstring(_clean_xml(xml_text), parser=parser)
    except etree.XMLSyntaxError as e:
        logger.error("Ledger XML parse error: %s", e)
        return results

    # optional logging (kept minimal)
    if parser.error_log:
        for err in parser.error_log[:5]:
            logger.warning(f"Ledger XML recover issue: {err}")

    for ledger in root.iter("LEDGER"):
        name   = ledger.get("NAME", "").strip()
        parent = _text(ledger, "PARENT")
        gstin  = _text(ledger, "GSTIN") or _text(ledger, "PARTYGSTIN") or None

        results.append({
            "account_name":      name,
            "source_account_id": name,
            "account_type":      _map_account_type(parent),
            "_is_party":         parent in ("Sundry Debtors", "Sundry Creditors"),
            "company_name":      name,
            "source_customer_id": name,
            "gstin":             gstin[:15] if gstin else None,
            "email":             _text(ledger, "EMAIL") or None,
            "phone":             _text(ledger, "LEDGERMOBILE") or None,
            "billing_address_jsonb": {"line1": _text(ledger, "ADDRESS")} if _text(ledger, "ADDRESS") else {},
        })
    return results


def parse_vouchers(xml_text):
    invoices, bills, payments = [], [], []
    try:
        parser = etree.XMLParser(recover=True, encoding="utf-8")  # ✅ FIX
        root = etree.fromstring(_clean_xml(xml_text), parser=parser)
    except etree.XMLSyntaxError as e:
        logger.error("Voucher XML parse error: %s", e)
        return invoices, bills, payments

    if parser.error_log:
        for err in parser.error_log[:5]:
            logger.warning(f"Voucher XML recover issue: {err}")

    for vch in root.iter("VOUCHER"):
        vch_type  = _text(vch, "VOUCHERTYPENAME").upper()
        vch_no    = _text(vch, "VOUCHERNUMBER")
        vch_date  = _date(_text(vch, "DATE"))
        party     = _text(vch, "PARTYLEDGERNAME")
        gstin     = _text(vch, "PARTYGSTIN") or None
        narration = _text(vch, "NARRATION")

        ledger_entries = vch.findall(".//ALLLEDGERENTRIES.LIST")
        line_items     = vch.findall(".//INVENTORYENTRIES.LIST") or \
                         vch.findall(".//ALLINVENTORYENTRIES.LIST")

        tax_amount, total = 0.0, 0.0
        for entry in ledger_entries:
            amt   = _amount(_text(entry, "AMOUNT"))
            lname = _text(entry, "LEDGERNAME")
            if any(t in lname.upper() for t in ["GST", "IGST", "CGST", "SGST", "TAX"]):
                tax_amount += abs(amt)
            elif lname == party:
                total = abs(amt)

        lines = []
        for i, inv in enumerate(line_items, start=1):
            qty_raw = _text(inv, "ACTUALQTY", "0")
            lines.append({
                "line_number":     i,
                "product_name":    _text(inv, "STOCKITEMNAME"),
                "hsn_code":        _text(inv, "HSNCODE") or None,
                "quantity":        float(qty_raw.split()[0]) if qty_raw else 0,
                "unit":            qty_raw.split()[1] if " " in qty_raw else None,
                "unit_price":      abs(_amount(_text(inv, "RATE", "0").split("/")[0])),
                "discount_amount": abs(_amount(_text(inv, "DISCOUNT", "0"))),
                "tax_rate_percent": 0.0,
                "tax_amount":      abs(_amount(_text(inv, "TAXAMOUNT", "0"))),
                "line_total":      abs(_amount(_text(inv, "AMOUNT", "0"))),
            })

        base = {
            "source": "TALLY", "source_updated_at": date.today().isoformat(),
            "narration": narration, "currency_code": "INR",
            "subtotal": total - tax_amount, "tax_amount": tax_amount,
            "total_amount": total, "sync_status": "PENDING",
            "validation_flag": "OK", "_party": party,
            "_gstin_party": gstin[:15] if gstin else None, "_lines": lines,
        }

        if vch_type == "SALES":
            invoices.append({**base, "source_invoice_id": vch_no,
                "invoice_number": vch_no, "invoice_date": vch_date,
                "due_date": None, "status": "SENT",
                "gstin_buyer": gstin[:15] if gstin else None})
        elif vch_type == "PURCHASE":
            bills.append({**base, "source_bill_id": vch_no,
                "bill_number": vch_no, "bill_date": vch_date,
                "due_date": None, "status": "DRAFT",
                "gstin_seller": gstin[:15] if gstin else None})
        elif vch_type in ("RECEIPT", "PAYMENT"):
            payments.append({
                "source": "TALLY", "source_payment_id": vch_no,
                "source_updated_at": date.today().isoformat(),
                "payment_date": vch_date, "amount": total,
                "currency_code": "INR", "transaction_ref": None,
                "status": "COMPLETED", "is_reconciled": False,
                "sync_status": "PENDING", "_party": party,
            })

    return invoices, bills, payments