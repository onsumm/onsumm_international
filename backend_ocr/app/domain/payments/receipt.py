"""What a vendor's payment receipt says, and the two forms it arrives in.

One `Receipt`, three renderings — the HTML mail body, its plain-text
alternative, and the attached PDF all read from the same object, so the
receipt a vendor reads in their inbox and the one they file cannot drift into
disagreeing about an amount or a reference. Same rule
frontend/src/pages/payment/receiptContent.ts already follows for the payer's
own copy of the document.

Nothing here touches the database, a mailbox or a request: it is pure
rendering, so the wording, the escaping and the arithmetic are testable on
their own. Building a `Receipt` out of `fact_disbursements` rows is
notify.py's job.

Two deliberate differences from the payer-facing receipt the Payment Hub
downloads:

  * the vendor's bank account is masked to the last four digits, and the
    remitter's account number never appears at all — this document leaves the
    building, so it names the *payer company*, not the account money left;
  * only settled money is on it. A PENDING or FAILED disbursement gets no
    receipt (notify.py never builds one), because "here is your receipt" for
    money that has not moved is the one thing a receipt must never say.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO


# ── Money ────────────────────────────────────────────────────────────────────

def group_inr(amount: float) -> str:
    """1234567.5 -> '12,34,567.50' — Indian grouping (last three, then twos).

    The en-IN grouping `Intl.NumberFormat` gives the UI (frontend/src/ui/format.ts).
    A vendor reading ₹12,34,567.50 against a bill they raised should see the
    digits fall where they expect them to.
    """
    negative = amount < 0
    whole, _, frac = f"{abs(float(amount)):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        whole = ",".join(pairs + [tail])
    return ("-" if negative else "") + whole + "." + frac


def inr(amount: float) -> str:
    """For HTML and plain text, both UTF-8, where ₹ renders."""
    return "₹" + group_inr(amount)


def rupees_ascii(amount: float) -> str:
    """For the PDF. Helvetica — reportlab's default, and the only font this
    codebase ships — has no ₹ glyph, so it would draw a black box. Every other
    PDF we generate writes 'Rs.' for the same reason (routers/bill_export.py)."""
    return "Rs. " + group_inr(amount)


def mask_account(account_number: str | None) -> str:
    """'50100123456789' -> '**********6789'.

    Mirrors routers/disbursement.py's `_mask_account`, which masks the same
    number for the payer's own screens. Kept here rather than imported because
    that module imports *this* package, and because on a document that leaves
    the building the masking is part of what the receipt says, not an
    incidental UI choice — the vendor gets enough to confirm it is their
    account and nothing an interceptor could reuse.
    """
    number = (account_number or "").strip()
    if not number:
        return ""
    return ("*" * max(0, len(number) - 4)) + number[-4:]


# ── The document ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReceiptBill:
    """One bill settled by the payment."""
    number: str
    amount: float
    date: str | None = None            # already formatted, e.g. "12 Sep 2026"
    reference: str | None = None       # the bank's RRN, else our own tran ref
    outstanding: float | None = None   # what is still open on this bill, if any


@dataclass(frozen=True)
class Receipt:
    payer_name: str
    vendor_name: str
    beneficiary_name: str
    beneficiary_account: str           # masked before it gets here
    paid_at: str                       # already formatted in the tenant's timezone
    bills: tuple[ReceiptBill, ...] = ()
    contact_name: str | None = None
    beneficiary_ifsc: str | None = None
    beneficiary_bank: str | None = None
    method: str = "IMPS bank transfer"

    @property
    def total(self) -> float:
        return sum(b.amount for b in self.bills)

    @property
    def references(self) -> list[str]:
        seen: list[str] = []
        for b in self.bills:
            if b.reference and b.reference not in seen:
                seen.append(b.reference)
        return seen


def subject(r: Receipt) -> str:
    """What the vendor sees in their inbox list, and all they may ever read.

    Amount and payer first, because a vendor chasing a payment is scanning for
    exactly those two things; the bill number only when there is a single one
    to name.
    """
    who = r.payer_name or "your customer"
    if len(r.bills) == 1 and r.bills[0].number:
        return f"Payment received: {inr(r.total)} from {who} against {r.bills[0].number}"
    return f"Payment received: {inr(r.total)} from {who}"


def file_name(r: Receipt) -> str:
    """A name the vendor can find again in their downloads folder — the same
    shape the payer's own download uses (receiptContent.ts:receiptFileName)."""
    import re
    who = re.sub(r"[^a-z0-9]+", "-", (r.vendor_name or "vendor").lower()).strip("-")[:40] or "vendor"
    day = r.paid_at.split(",")[0].replace(" ", "-").lower()
    ref = r.references[0] if r.references else ""
    return f"payment-receipt-{who}-{day}{'-' + ref.lower() if ref else ''}.pdf"


# ── Rows: the detail block both renderings lay out ───────────────────────────

def detail_rows(r: Receipt) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("Paid by", r.payer_name), ("Paid to", r.beneficiary_name)]
    if r.beneficiary_name.strip().lower() != (r.vendor_name or "").strip().lower():
        rows.append(("Vendor", r.vendor_name))
    account = " · ".join(x for x in (r.beneficiary_bank, r.beneficiary_account) if x)
    if account:
        rows.append(("Bank account", account))
    if r.beneficiary_ifsc:
        rows.append(("IFSC", r.beneficiary_ifsc))
    rows.append(("Paid on", r.paid_at))
    rows.append(("Method", r.method))
    refs = r.references
    if len(refs) == 1:
        rows.append(("Bank reference (UTR)", refs[0]))
    return rows


# ── Plain text ───────────────────────────────────────────────────────────────

def render_text(r: Receipt) -> str:
    """The text/plain half of the mail.

    Not a courtesy: a receipt whose only body is HTML reads as an empty message
    in a text-only client and scores worse with spam filters, and this mail has
    to arrive.
    """
    hello = f"Hi {r.contact_name.split()[0]}," if r.contact_name else "Hello,"
    lines = [
        hello,
        "",
        f"{r.payer_name} has paid {inr(r.total)} to {r.beneficiary_name}. "
        "A receipt is attached as a PDF.",
        "",
        "PAYMENT DETAILS",
    ]
    lines += [f"  {label}: {value}" for label, value in detail_rows(r)]
    lines += ["", "BILLS SETTLED"]
    for b in r.bills:
        head = f"  {b.number} — {inr(b.amount)}"
        if b.date:
            head += f" (dated {b.date})"
        lines.append(head)
        if b.reference and len(r.references) > 1:
            lines.append(f"      Bank reference: {b.reference}")
        if b.outstanding and b.outstanding > 0.01:
            lines.append(f"      Still open on this bill: {inr(b.outstanding)}")
    lines += [
        "",
        f"Total paid: {inr(r.total)}",
        "",
        "This receipt is generated from the payment record and is not a bank "
        "statement. If anything here looks wrong, reply to this email.",
    ]
    return "\n".join(lines)


# ── HTML ─────────────────────────────────────────────────────────────────────

_INK = "#1e3a5f"      # the heading navy routers/bill_export.py's PDF uses
_MUTED = "#6b7280"
_LINE = "#e5e7eb"
_PAID = "#065f46"
_PAID_BG = "#d1fae5"


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def render_html(r: Receipt) -> str:
    """A receipt-style body, built the way mail clients still demand: tables for
    layout and every style inline. Gmail and Outlook both drop a <style> block,
    so a class-based version of this arrives unstyled."""
    hello = f"Hi {_esc(r.contact_name.split()[0])}," if r.contact_name else "Hello,"

    details = "".join(
        f'<tr>'
        f'<td style="padding:7px 0;color:{_MUTED};font-size:13px;white-space:nowrap;'
        f'vertical-align:top;">{_esc(label)}</td>'
        f'<td style="padding:7px 0 7px 16px;color:#111827;font-size:13px;'
        f'text-align:right;">{_esc(value)}</td>'
        f'</tr>'
        for label, value in detail_rows(r)
    )

    bill_rows = []
    for b in r.bills:
        sub = []
        if b.date:
            sub.append(f"dated {_esc(b.date)}")
        if b.reference and len(r.references) > 1:
            sub.append(f"ref {_esc(b.reference)}")
        if b.outstanding and b.outstanding > 0.01:
            sub.append(f"{_esc(inr(b.outstanding))} still open")
        caption = (
            f'<div style="color:{_MUTED};font-size:11px;padding-top:2px;">'
            f'{" &middot; ".join(sub)}</div>' if sub else ""
        )
        bill_rows.append(
            f'<tr>'
            f'<td style="padding:10px 0;border-top:1px solid {_LINE};font-size:13px;color:#111827;">'
            f'<b>{_esc(b.number)}</b>{caption}</td>'
            f'<td style="padding:10px 0;border-top:1px solid {_LINE};font-size:13px;'
            f'color:#111827;text-align:right;white-space:nowrap;vertical-align:top;">'
            f'{_esc(inr(b.amount))}</td>'
            f'</tr>'
        )

    total_row = (
        f'<tr>'
        f'<td style="padding:10px 0;border-top:2px solid {_LINE};font-size:13px;'
        f'color:#111827;"><b>Total paid</b></td>'
        f'<td style="padding:10px 0;border-top:2px solid {_LINE};font-size:13px;'
        f'color:#111827;text-align:right;"><b>{_esc(inr(r.total))}</b></td>'
        f'</tr>'
    ) if len(r.bills) > 1 else ""

    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:24px 12px;background:#f3f4f6;
 font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
<tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
 style="max-width:560px;width:100%;background:#ffffff;border:1px solid {_LINE};
 border-radius:10px;overflow:hidden;">

  <tr><td style="padding:22px 28px;background:{_INK};">
    <div style="color:#ffffff;font-size:16px;font-weight:600;">Payment receipt</div>
    <div style="color:#c7d5e6;font-size:12px;padding-top:2px;">{_esc(r.payer_name)}</div>
  </td></tr>

  <tr><td style="padding:26px 28px 6px;">
    <p style="margin:0 0 14px;font-size:14px;color:#374151;">{hello}</p>
    <p style="margin:0 0 18px;font-size:14px;color:#374151;line-height:1.5;">
      {_esc(r.payer_name)} has paid the amount below to
      <b>{_esc(r.beneficiary_name)}</b>. The same receipt is attached as a PDF
      for your records.
    </p>
    <div style="font-size:30px;font-weight:700;color:{_INK};letter-spacing:-0.5px;">
      {_esc(inr(r.total))}
    </div>
    <div style="display:inline-block;margin-top:8px;padding:3px 10px;border-radius:999px;
     background:{_PAID_BG};color:{_PAID};font-size:11px;font-weight:600;">
      Paid &middot; {_esc(r.paid_at)}
    </div>
  </td></tr>

  <tr><td style="padding:22px 28px 0;">
    <div style="font-size:11px;font-weight:700;color:{_MUTED};letter-spacing:0.6px;
     text-transform:uppercase;padding-bottom:4px;">Payment details</div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
      {details}
    </table>
  </td></tr>

  <tr><td style="padding:22px 28px 0;">
    <div style="font-size:11px;font-weight:700;color:{_MUTED};letter-spacing:0.6px;
     text-transform:uppercase;padding-bottom:4px;">Bills settled</div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
      {"".join(bill_rows)}
      {total_row}
    </table>
  </td></tr>

  <tr><td style="padding:24px 28px 26px;">
    <p style="margin:0;font-size:11px;color:{_MUTED};line-height:1.6;">
      This receipt is generated from the payment record and is not a bank
      statement. Funds usually appear in the account within a few minutes of
      the time above. If anything here looks wrong, reply to this email.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


# ── PDF ──────────────────────────────────────────────────────────────────────

def render_pdf(r: Receipt) -> bytes:
    """A one-page receipt, laid out like routers/bill_export.py's activity page
    so every PDF this product emails looks like it came from the same place."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title="Payment receipt", author=r.payer_name,
    )
    base = getSampleStyleSheet()["Normal"]
    # Explicit leading on the large type: the default (12pt) is shorter than the
    # font, so the line under it was drawn over it.
    h1 = ParagraphStyle("H1", parent=base, fontSize=18, leading=22, fontName="Helvetica-Bold",
                        textColor=colors.HexColor(_INK), spaceAfter=4)
    meta = ParagraphStyle("Meta", parent=base, fontSize=9, textColor=colors.HexColor(_MUTED),
                          spaceAfter=12)
    amount = ParagraphStyle("Amt", parent=base, fontSize=24, leading=30, fontName="Helvetica-Bold",
                            textColor=colors.HexColor(_INK), spaceBefore=12, spaceAfter=2)
    h2 = ParagraphStyle("H2", parent=base, fontSize=11, fontName="Helvetica-Bold",
                        textColor=colors.HexColor(_INK), spaceBefore=14, spaceAfter=6)
    cell = ParagraphStyle("Cell", parent=base, fontSize=9, leading=12)
    cell_r = ParagraphStyle("CellR", parent=cell, alignment=2)
    label = ParagraphStyle("Label", parent=cell, textColor=colors.HexColor(_MUTED))
    foot = ParagraphStyle("Foot", parent=base, fontSize=8, leading=11,
                          textColor=colors.HexColor(_MUTED), spaceBefore=18)

    # Tables fit the frame's inner width (the frame pads 6pt a side); sized to the
    # full margin box they overflowed and sat left of the headings.
    width = doc.width - 12

    story = [
        Paragraph("Payment receipt", h1),
        Paragraph(f"{_esc(r.payer_name)} &nbsp;|&nbsp; {_esc(r.paid_at)}", meta),
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(_LINE)),
        Paragraph(rupees_ascii(r.total), amount),
        Paragraph(f"paid to {_esc(r.beneficiary_name)}", meta),
        Paragraph("Payment details", h2),
    ]

    detail_table = Table(
        [[Paragraph(_esc(k), label), Paragraph(_esc(v), cell_r)] for k, v in detail_rows(r)],
        colWidths=[55 * mm, width - 55 * mm],
    )
    detail_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [detail_table, Paragraph("Bills settled", h2)]

    hdr = ParagraphStyle("Hdr", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    rows = [[Paragraph("Bill", hdr), Paragraph("Bill date", hdr),
             Paragraph("Bank reference", hdr),
             Paragraph("Amount paid", ParagraphStyle("HdrR", parent=hdr, alignment=2))]]
    for b in r.bills:
        rows.append([
            Paragraph(_esc(b.number), cell),
            Paragraph(_esc(b.date or "-"), cell),
            Paragraph(_esc(b.reference or "-"), cell),
            Paragraph(rupees_ascii(b.amount), cell_r),
        ])
    if len(r.bills) > 1:
        rows.append([Paragraph("<b>Total paid</b>", cell), Paragraph("", cell), Paragraph("", cell),
                     Paragraph(f"<b>{rupees_ascii(r.total)}</b>", cell_r)])

    bills_table = Table(rows, colWidths=[w * width for w in (0.30, 0.16, 0.31, 0.23)], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_INK)),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(_LINE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if len(r.bills) > 1:
        style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f9fafb")))
    bills_table.setStyle(TableStyle(style))
    story.append(bills_table)

    open_bills = [b for b in r.bills if b.outstanding and b.outstanding > 0.01]
    if open_bills:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "Still open after this payment: " + ", ".join(
                f"{_esc(b.number)} {rupees_ascii(b.outstanding or 0)}" for b in open_bills),
            ParagraphStyle("Open", parent=cell, textColor=colors.HexColor(_MUTED)),
        ))

    story.append(Paragraph(
        "This receipt is generated from the payment record and is not a bank statement. "
        "Funds usually appear in the account within a few minutes of the time above. "
        "If anything here looks wrong, reply to the email this was attached to.",
        foot,
    ))

    doc.build(story)
    return buf.getvalue()


# ── Formatting helpers notify.py builds a Receipt with ───────────────────────

def format_day(value) -> str | None:
    """A bill date as '12 Sep 2026', or None when there isn't one."""
    return value.strftime("%d %b %Y") if value else None


def format_moment(value: datetime | None, tz_name: str = "Asia/Kolkata") -> str:
    """A payment time in the tenant's own timezone, named so the vendor can
    reconcile it against their bank feed without guessing the offset."""
    from zoneinfo import ZoneInfo
    if value is None:
        return ""
    try:
        zone = ZoneInfo(tz_name or "Asia/Kolkata")
    except Exception:
        zone = ZoneInfo("Asia/Kolkata")
    if value.tzinfo is None:
        from datetime import timezone as _tz
        value = value.replace(tzinfo=_tz.utc)
    local = value.astimezone(zone)
    return local.strftime("%d %b %Y, %H:%M ") + (local.tzname() or "")
