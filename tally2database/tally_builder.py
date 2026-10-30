"""
tally_builder.py — Python dicts → Tally Prime import XML
"""
from datetime import date as date_type
from xml.sax.saxutils import escape


def _tally_date(d):
    if isinstance(d, date_type):
        return d.strftime("%Y%m%d")
    if isinstance(d, str) and len(d) == 10:
        return d.replace("-", "")
    return str(d)


def _x(value):
    if value is None:
        return ""
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def build_sales_voucher(voucher_number, invoice_date, party_name,
                        sales_ledger, line_items, tax_ledger,
                        tax_amount, total_amount, narration=""):
    include_tax = bool(tax_ledger) and float(tax_amount or 0) > 0
    sales_amount = float(total_amount) - float(tax_amount or 0) if include_tax else float(total_amount)

    tax_xml = f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{_x(tax_ledger)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{tax_amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>""" if include_tax else ""

    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
        <DATE>{_tally_date(invoice_date)}</DATE>
        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <VOUCHERNUMBER>{_x(voucher_number)}</VOUCHERNUMBER>
        <PARTYLEDGERNAME>{_x(party_name)}</PARTYLEDGERNAME>
        <NARRATION>{_x(narration)}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{_x(party_name)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{total_amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{_x(sales_ledger)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{sales_amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        {tax_xml}
      </VOUCHER>
    </TALLYMESSAGE>"""


def build_receipt_voucher(voucher_number, payment_date, party_name,
                          bank_ledger, amount, narration="", transaction_ref=""):
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <VOUCHER VCHTYPE="Receipt" ACTION="Create">
        <DATE>{_tally_date(payment_date)}</DATE>
        <VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME>
        <VOUCHERNUMBER>{_x(voucher_number)}</VOUCHERNUMBER>
        <NARRATION>{_x(narration)} {_x(transaction_ref)}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{_x(bank_ledger)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{_x(party_name)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE>"""


def build_ledger_master(ledger_name, group_name, gstin="", opening_balance=0.0):
    return f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <LEDGER NAME="{_x(ledger_name)}" ACTION="Create">
        <NAME>{_x(ledger_name)}</NAME>
        <PARENT>{_x(group_name)}</PARENT>
        {"<GSTIN>" + _x(gstin) + "</GSTIN>" if gstin else ""}
        <OPENINGBALANCE>{opening_balance}</OPENINGBALANCE>
      </LEDGER>
    </TALLYMESSAGE>"""
