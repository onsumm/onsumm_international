"""
Generate a synthetic Signzy tax-invoice PDF for testing the Nova billing
reconciler end-to-end. Modeled visually on the real Signzy invoice format
(SIGNZY2526/08775) but for the 2026-05 service month.

IMPORTANT: this Nova sandbox's "live" numbers for a given month can drift
between pulls (observed firsthand: 2026-05 eSign billable ops went from 72 in
the originally-committed sample JSON to 70,624 on a later live pull). The qty
values below were calibrated against a live pull taken immediately before
registering this invoice (2026-07-13) — if you regenerate this fixture later,
re-pull Nova for 2026-05 first and recalculate, or the qty-exact lines below
(eSign, Image Liveliness, Bank Aggregator) will drift into QTY_MISMATCH
instead of cleanly testing RATE_MISMATCH/COST_MISMATCH.

Deliberately seeded to exercise every discrepancy class the reconciler
(pipelines/billing_reconciliation.py) detects, calibrated against the live
2026-05 Nova pull so each line lands in exactly the intended bucket (a qty
mismatch short-circuits the rate/cost checks in _compare_line, so RATE_/
COST_MISMATCH require an EXACT qty match to demonstrate cleanly):

  Line 1  Bank Aggregator      — exact match (34,389 @ Rs.4.00)              -> GREEN control
  Line 2  Aadhaar OCR          — no curated alias pattern exists for this    -> UNMAPPED_LINE_ITEM (#1)
                                  module yet; second unmapped line in the
                                  same run, deliberately, to test that two
                                  UNMAPPED_LINE_ITEM findings (both have
                                  nova_module=NULL) don't collide and
                                  overwrite each other — they're disambiguated
                                  by the invoice line's own description.
  Line 3  eSign                — qty matches exactly (70,624),               -> RATE_MISMATCH
                                  rate Rs.10.50 vs Nova's Rs.9.29
  Line 4  Image Liveliness     — qty (164,772) and rate (Rs.1.19) both match -> COST_MISMATCH
                                  Nova exactly, but amount doesn't multiply
                                  out (Rs.230,000 printed vs Rs.196,078.68 —
                                  ~17%, clearly over the 5% tolerance)
  Line 5  Penny Drop           — 1,324,500 vs Nova's 88,300 (~15x)           -> QTY_MISMATCH (CRITICAL)
  Line 6  Company Name to GST  — Nova has zero usage this month              -> MISSING_IN_NOVA
  Line 7  Platform Access Fee  — no alias pattern matches this description   -> UNMAPPED_LINE_ITEM (#2)

  Omitted entirely (Nova has real usage, invoice never bills for it):
    PAN (331,084 ops), PAN OCR (704), Name Match (4,231),
    Email Verification (569,278), Face Match (120,883), eKYC (239,090)      -> MISSING_IN_INVOICE x6
"""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT

OUT_PATH = Path(__file__).resolve().parent.parent / "lendix" / "SIGNZY_TEST_2026-05_reconciliation.pdf"

styles = getSampleStyleSheet()
small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
right = ParagraphStyle("right", parent=small, alignment=TA_RIGHT)
h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=0)
h2 = ParagraphStyle("h2", parent=styles["Heading3"], fontSize=9, textColor=colors.HexColor("#555555"))

LINES = [
    # (sl, description, category, subtag, hsn, qty, rate, igst_pct, amount, note)
    # Calibrated against a live Nova pull for 2026-05 taken 2026-07-13 — see module docstring.
    (1, "Bank Aggregator API\navinashcapital_prod_v3", "API", "Pay per use", "998314", 34389.00, 4.00, 18, 137556.00, "exact match"),
    (2, "Aadhaar OCR Verification\navinashcapital_prod_v3", "API", "Pay per use", "998314", 9.00, 0.79, 18, 7.11, "unmapped (no curated alias yet) — 2nd unmapped line, tests dedup-by-description fix"),
    (3, "eSign Digital Document\navinashcapital_prod_v3", "API", "Pay per use", "998314", 70624.00, 10.50, 18, 741552.00, "rate mismatch"),
    (4, "Image Liveliness Check\navinashcapital_prod_v3", "API", "Pay per use", "998314", 164772.00, 1.19, 18, 230000.00, "cost mismatch (correct amount=196078.68, >5% tolerance)"),
    (5, "Penny Drop Verification\navinashcapital_prod_v3", "API", "Pay per use", "998314", 1324500.00, 2.39, 18, 3165555.00, "critical overbill"),
    (6, "Company Name To GST\navinashcapital_prod_v3", "API", "Pay per use", "998314", 1.00, 0.60, 18, 0.60, "missing in Nova"),
    (7, "Platform Access & Compliance Fee\navinashcapital_prod_v3", "Misc", "One-time", "998314", 1.00, 500.00, 18, 500.00, "unmapped line item"),
]

SUBTOTAL = sum(l[8] for l in LINES)
IGST = round(SUBTOTAL * 0.18, 2)
TOTAL = round(SUBTOTAL + IGST, 2)


def build():
    doc = SimpleDocTemplate(str(OUT_PATH), pagesize=A4, topMargin=14 * mm, bottomMargin=14 * mm,
                             leftMargin=14 * mm, rightMargin=14 * mm)
    story = []

    # Header
    header_tbl = Table([
        [Paragraph("<b>SIGNZY</b>", h1), Paragraph("<b>TAX INVOICE</b>", h1)],
        [Paragraph(
            "Signzy Technologies Pvt. Ltd.<br/>Awfis, 8th Floor, Tower 1, One International Centre<br/>"
            "Senapati Bapat Marg, Dadar West, Prabhadevi<br/>Mumbai Maharashtra 400013, India<br/>"
            "GSTIN 27AAWCS3552Q1ZA &nbsp; billing@signzy.com", small),
         Table([
             ["Invoice No.", "SIGNZY2526/TEST-0501"],
             ["Invoice Date", "05/06/2026"],
             ["Terms", "Net 15"],
             ["Due Date", "20/06/2026"],
             ["Place Of Supply", "Delhi (07)"],
             ["Billing Period", "May 2026"],
             ["Region", "India"],
         ], colWidths=[35 * mm, 45 * mm], style=TableStyle([
             ("FONTSIZE", (0, 0), (-1, -1), 8),
             ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
             ("TOPPADDING", (0, 0), (-1, -1), 2),
         ]))],
    ], colWidths=[95 * mm, 82 * mm])
    header_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(header_tbl)
    story.append(Spacer(1, 8 * mm))

    # Bill To
    bill_to = Paragraph(
        "<b>Bill To / Ship To</b><br/>"
        "<b>AVINASH CAPITAL MARKETS PRIVATE LIMITED</b><br/>"
        "3RD FLOOR Flat No.: 68 Okhla Phase 3 Road<br/>"
        "Okhla Industrial Estate Phase 3 New Delhi South East Delhi<br/>"
        "Delhi 110020, India<br/>"
        "GSTIN 07AACCA2775M1ZF", small)
    story.append(bill_to)
    story.append(Spacer(1, 6 * mm))

    # Line items table
    head = ["Sl", "Item & Description", "Category", "Sub tag", "HSN/SAC", "Qty", "Rate", "IGST%", "Amount"]
    rows = [head]
    for sl, desc, cat, sub, hsn, qty, rate, igst_pct, amt, _note in LINES:
        rows.append([
            str(sl), Paragraph(desc, small), cat, sub, hsn,
            f"{qty:,.2f}", f"{rate:,.2f}", f"{igst_pct}%", f"{amt:,.2f}",
        ])
    line_tbl = Table(rows, colWidths=[8*mm, 42*mm, 15*mm, 20*mm, 18*mm, 22*mm, 16*mm, 12*mm, 22*mm], repeatRows=1)
    line_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (5, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(line_tbl)
    story.append(Spacer(1, 6 * mm))

    # Totals
    totals_tbl = Table([
        ["Sub Total", f"{SUBTOTAL:,.2f}"],
        ["IGST (18%)", f"{IGST:,.2f}"],
        ["Total", f"{TOTAL:,.2f}"],
    ], colWidths=[40 * mm, 30 * mm])
    totals_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    wrapper = Table([[Spacer(1, 1), totals_tbl]], colWidths=[125 * mm, 70 * mm])
    story.append(wrapper)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph(
        f"<b>Total In Words:</b> Indian Rupee {_num_to_words(int(TOTAL))} and "
        f"{round((TOTAL - int(TOTAL)) * 100):02d} Paise Only", small))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "<b>Company's Bank Details</b><br/>Bank Name: Axis Bank, Agra<br/>"
        "A/c No.: 915020057637889<br/>Branch &amp; IFS Code: Sanjay Place, Agra &amp; UTIB0000086", small))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "<b>Terms &amp; Conditions</b><br/>1. Invoice must be paid within 30 days.<br/>"
        "2. For GST purpose, we will officially upload this invoice post payment confirmation.<br/>"
        "<br/><i>[SYNTHETIC TEST DOCUMENT — generated for Nova billing reconciliation testing, "
        "not a real Signzy invoice.]</i>", small))

    doc.build(story)
    print(f"Wrote {OUT_PATH}")
    print(f"Subtotal={SUBTOTAL:.2f} IGST={IGST:.2f} Total={TOTAL:.2f}")


def _num_to_words(n: int) -> str:
    # Minimal INR words for totals in the tens-of-thousands range — good enough for a test doc.
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two(x):
        if x < 20:
            return ones[x]
        return tens[x // 10] + (f" {ones[x % 10]}" if x % 10 else "")

    def three(x):
        if x >= 100:
            return f"{ones[x // 100]} Hundred" + (f" {two(x % 100)}" if x % 100 else "")
        return two(x)

    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore: parts.append(f"{three(crore)} Crore")
    if lakh: parts.append(f"{three(lakh)} Lakh")
    if thousand: parts.append(f"{three(thousand)} Thousand")
    if n: parts.append(three(n))
    return " ".join(parts)


if __name__ == "__main__":
    build()
