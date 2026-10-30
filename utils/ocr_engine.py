"""
Shared GPT-4o document-extraction engine.

Used by both the async pipeline worker (pipelines/upload_ocr.py)
and the FastAPI upload server (backend_ocr/app/ocr.py).

Two read paths feed the same GPT-4o structuring step, chosen by config.OCR_TO_USE:

  text   PDF → embedded text layer (pymupdf, threadpool) → GPT-4o → structured JSON
  vision PDF → page images (cpu, threadpool) → GPT-4o vision → structured JSON

"auto" (the default) prefers the text layer and falls back to vision when the PDF
has none — a scan. Digital invoices carry their exact characters, so the text path
cannot misread a digit, which is where vision actually fails: measured against real
bills it garbled a GSTIN on 2 of 4 documents, and dropped a "/" from an invoice
number. It is also consistently faster. Token cost is roughly a wash — a dense
multi-page text layer can exceed the cost of the same pages as downscaled images.
Vision remains the only option for scans and for standalone images.
"""
from __future__ import annotations

import asyncio
import contextvars
import base64
import io
import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
_client  = None   # lazy-init: avoids import error when openai is not installed


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


_SCHEMA_AND_RULES = """
Schema:
{
  "document_type": "invoice|bill|receipt|ledger|statement|other",
  "invoice_number": "",
  "invoice_date": "",
  "due_date": "",
  "service_month": "",
  "vendor":  { "name":"","address":"","gstin":"","pan":"","phone":"","email":"" },
  "buyer":   { "name":"","address":"","gstin":"","pan":"" },
  "place_of_supply":"",
  "line_items": [
    { "description":"","hsn_sac":"","quantity":null,"unit":"","rate":null,"rate_is_percent":false,"amount":null,"service_month":"" }
  ],
  "taxes": {
    "taxable_amount":null,
    "cgst_rate":null,"cgst_amount":null,
    "sgst_rate":null,"sgst_amount":null,
    "igst_rate":null,"igst_amount":null,
    "cess_amount":null,
    "total_tax_amount":null
  },
  "subtotal":null,"discount":null,"total_amount":null,
  "currency_code":"INR",
  "amount_in_words":"","payment_terms":"",
  "bank_details":{"bank_name":"","account_no":"","ifsc":"","branch":""},
  "notes":"","raw_fields":{}
}

Rules:
- Return ONLY the JSON object — no markdown, no prose.
- Numbers: plain float/int, no commas, no currency symbols.
- Dates: DD-MM-YYYY where possible.
- GSTIN: exactly 15 chars, copy verbatim.
- Merge data across all pages.
- currency_code: detect from symbols ($→USD, €→EUR, £→GBP, ¥→JPY, ₹→INR) or explicit text (USD/EUR/GBP/JPY/INR). Default INR if not found.
- COLUMN POSITION IS AUTHORITATIVE. Read the column headers (Qty, Rate, Amount, ...)
  and assign each number to the column it physically sits under. NEVER swap two values
  because one "looks more like" a quantity or a price. Usage-based invoices legitimately
  bill a huge quantity at a tiny unit rate (e.g. 129558 data fetches × ₹4.00), so a
  quantity may be far larger than the rate. Trust the layout, not plausibility.
- Indian digit grouping: "1,29,558.00" is 129558.00 — NOT 1 and 29,558. Strip ALL commas
  before reading a number; a comma NEVER separates two columns.
- PERCENTAGE RATES. Some lines are priced as a percentage of a base rather than at a
  unit price — commission on sales, a platform or collection fee, brokerage, interest
  on an overdue amount. You can recognise them by a "%" printed in the Rate column, or
  wording like "@ 2% on ...", "Commission @ 4%", "2.5% of gross value".
  For such a line set rate_is_percent true, put the PERCENT NUMBER in `rate` (4 for
  "4%", never 0.04) and the BASE AMOUNT the percent applies to in `quantity`. The
  `amount` is then base x percent / 100, which is what the document prints.
  Example — "Commission on April sales @ 4% on 1,00,000 ... 4,000.00" becomes
  quantity 100000, rate 4, rate_is_percent true, amount 4000.
  Leave rate_is_percent false for every ordinary line, including one whose unit
  happens to be a percentage-like token.
- subtotal: the PRE-TAX taxable value that the tax amounts are actually computed on —
  this includes freight / packing / other charges listed after the goods lines, so it
  is NOT always the sum of the line items. Set taxes.taxable_amount to the same value.
- place_of_supply: copy the "Place of Supply" line verbatim if the invoice prints one,
  e.g. "Haryana (06)" or "07 Delhi". It is the state whose GST applies, and it is NOT
  always the buyer's address. Return "" when the invoice does not state it — do not
  infer it from an address or a GSTIN.
- GST. Read the tax table as it is printed and do not rearrange it.
  · CGST and SGST always appear TOGETHER and never alongside IGST — that pair is an
    intra-state supply. IGST appears ALONE, for an inter-state one. Never return all
    three. If the document shows CGST 9% and SGST 9%, that is two 9% lines, NOT one
    at 18%.
  · Rates are the printed percentage NUMBER: 9 for "9%", 2.5 for "2.5%", never 0.09.
  · UTGST is SGST for a union territory. Put it in the sgst_rate / sgst_amount fields.
  · total_tax_amount: the tax total the document itself prints — a "Total Tax", "Total
    GST" or "Tax Amount" line. Copy it verbatim; do NOT add the components up yourself
    to fill it. Leave it null when the document prints no such line.
  · When the invoice prints only a combined figure, e.g. "GST @ 18% ... 864.22", with
    no CGST/SGST split anywhere: put that figure in total_tax_amount and leave the
    components null. Do NOT invent a split. An 18% line is CGST 9 + SGST 9 only when
    the document says so.
  · taxable_amount is the value the tax was computed ON, not the invoice total.
- service_month: the SERVICE / BILLING month the invoice covers — this is DIFFERENT from the invoice date. Look for labels like "Billing Period", "Service Month", "Service Period", "SER MON", "Period", "For the month of", "Usage Month", or a Period/Month column, or a month named in a line's description. Return "YYYY-MM". For a date range, use the START month. If only a month name is given, infer the year from the invoice date. If a line item names its own month, set that line's service_month; otherwise leave it "". If no service/billing month appears anywhere, return "" — do NOT guess or copy the invoice date.
"""

_ARITHMETIC_RULES_VISION = """- Line-item arithmetic: for EACH line, quantity × rate must equal amount (for a
  rate_is_percent line the identity is quantity × rate / 100 = amount). Read every digit
  carefully — do NOT insert or drop digits (e.g. ₹2,02,131.86 is 202131.86, not 2021311.86).
  After extracting a line, multiply quantity × rate and compare to the printed amount; if they
  disagree, re-examine the image and correct whichever of the three values you misread.
- The line amounts must sum to the subtotal (pre-tax total). If your extracted lines don't sum
  to the printed subtotal, re-check the lines for a misread digit before returning.
- Missing field → null (numbers) or "" (strings)."""

# The text layer carries exact characters, so there is no misreading to repair —
# guessing is the only failure mode left. A quantity × rate ≠ amount line is then
# real (a discount or per-unit column), NOT a digit slip, so it must be preserved.
_ARITHMETIC_RULES_TEXT = """- Copy every number EXACTLY as it appears in the text. Never recompute, round, or
  "correct" a printed value, and never invent a digit that is not there.
- Do NOT force quantity × rate to equal amount (on a rate_is_percent line the
  identity is quantity × rate / 100). When they genuinely differ the
  document means it (a discount, or a rate quoted per different unit) — record all
  three as printed.
- Column alignment is preserved by spacing, but a row's values may wrap onto the
  next line. Associate each number with its correct column heading before using it.
- A SINGLE cell's number may be split across two lines — "1,29,558." on the item row
  with its trailing "00" on the line below is one value, 129558.00. Rejoin such
  fragments before reading them, and do not mistake a fragment for a whole number.
- Names come from an accounting package's ledger, so they may carry a trailing
  bookkeeping artifact — a date suffix, an "(opening)" tag, a trailing dash. Record
  the party's actual legal name and drop such suffixes (e.g. "C. B. COMPANY- (1-Apr-23)"
  → "C. B. COMPANY"). This is the ONE case where you may clean a printed value; never
  alter a number, GSTIN, PAN, or invoice number this way.
- If a value is absent from the text, use null / "" — do not infer it from other
  fields.
- Missing field → null (numbers) or "" (strings)."""

# ── Jurisdiction hint ─────────────────────────────────────────────────────────
# The prompts below are written for Indian GST invoices (GSTIN, CGST/SGST/IGST,
# place of supply). A tenant outside India (backend_ocr/app/core/jurisdiction.py)
# mostly receives VAT / sales-tax invoices, where those rules would make the
# model invent a GST split or squeeze a VAT number into the GSTIN field. Callers
# set the tenant's jurisdiction before OCR; an Indian tenant (the default) gets
# the prompts byte-for-byte unchanged.
_OCR_JURISDICTION: contextvars.ContextVar = contextvars.ContextVar(
    "ocr_jurisdiction", default=("india", "IN"))


def set_ocr_jurisdiction(profile: str | None, country_code: str | None = None) -> None:
    """Set the jurisdiction for OCR calls made from this task onwards."""
    _OCR_JURISDICTION.set(((profile or "india").lower(), (country_code or "IN").upper()))


_INTERNATIONAL_ADDENDUM = """

JURISDICTION: the business receiving this document is outside India ({country}).
- The GST rules above apply ONLY to an Indian GST invoice (one that prints a
  15-character GSTIN and CGST / SGST / IGST). For any other invoice:
  · put the VAT / sales tax / non-Indian GST total in total_tax_amount, and the value
    it was computed on in taxes.taxable_amount, exactly as printed;
  · leave every cgst_*, sgst_*, igst_* field null and place_of_supply "";
  · leave gstin and pan "" and record the seller's own tax ID (VAT number, TRN, EIN,
    UEN, ABN ...) in vendor.tax_id.
- currency: the ISO 4217 code the document is priced in (USD, EUR, SGD, AED ...).
  Never assume INR."""


def _with_jurisdiction(prompt: str) -> str:
    profile, country = _OCR_JURISDICTION.get()
    if profile == "india":
        return prompt
    return prompt + _INTERNATIONAL_ADDENDUM.format(country=country)


SYSTEM_PROMPT = ("""You are an expert document parser specialising in Indian GST invoices.
You will receive one or more page images. Extract ALL structured data and return ONLY valid JSON.
""" + _SCHEMA_AND_RULES + _ARITHMETIC_RULES_VISION)

TEXT_SYSTEM_PROMPT = ("""You are an expert document parser specialising in Indian GST invoices.
You will receive the text layer extracted verbatim from a PDF, page by page, with the
original spacing preserved so table columns line up. This text is character-exact —
it is what the document literally contains, not an OCR guess. Extract ALL structured
data and return ONLY valid JSON.
""" + _SCHEMA_AND_RULES + _ARITHMETIC_RULES_TEXT)


def _content_or_raise(resp, what: str) -> str:
    """Return the completion text, or raise a diagnosable error.

    A refusal (or a stop on a content filter) leaves `content` None, which otherwise
    reaches json.loads and surfaces as an opaque
    "the JSON object must be str, bytes or bytearray, not NoneType".
    Logo-dense letterhead crops trigger this occasionally.
    """
    choice = resp.choices[0]
    content = choice.message.content
    if content:
        return content
    refusal = getattr(choice.message, "refusal", None)
    raise ValueError(
        f"{what}: model returned no content "
        f"(finish_reason={choice.finish_reason!r}"
        + (f", refusal={refusal!r}" if refusal else "") + ")"
    )


def _encode_page(img, max_short: int = 1024) -> str:
    """Resize so the short side = max_short, then base64-encode as PNG."""
    w, h = img.size
    short = min(w, h)
    if short != max_short:
        scale = max_short / short
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


async def _call_vision(b64_pages: list[str], filename: str) -> dict:
    """Single GPT-4o call with all page images."""
    content: list[dict] = [
        {"type": "text", "text": f"Document: {filename}. Extract all invoice data."},
    ]
    for b64 in b64_pages:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })
    resp = await _get_client().chat.completions.create(
        model=config.OPENAI_OCR_MODEL,
        messages=[
            {"role": "system", "content": _with_jurisdiction(SYSTEM_PROMPT)},
            {"role": "user",   "content": content},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=4096,
    )
    data = json.loads(_content_or_raise(resp, "vision extraction"))
    _apply_service_month(data)
    _reconcile_line_amounts(data)
    return data


async def _call_text(pages: list[str], filename: str) -> dict:
    """Single GPT-4o call over the PDF's extracted text layer.

    Character-exact: a text-layer digit cannot be misread, so no amount repair is
    applied to the result. Faster than the vision path; token cost is comparable.
    """
    body = "\n\n".join(
        f"--- Page {n} ---\n{txt}" for n, txt in enumerate(pages, start=1)
    )
    resp = await _get_client().chat.completions.create(
        model=config.OPENAI_OCR_MODEL,
        messages=[
            {"role": "system", "content": _with_jurisdiction(TEXT_SYSTEM_PROMPT)},
            {"role": "user",
             "content": f"Document: {filename}\n\n{body}"},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=4096,
    )
    data = json.loads(_content_or_raise(resp, "text extraction"))
    _apply_service_month(data)
    _reconcile_line_amounts(data, trust_printed_amounts=True)
    return data


def _to_num(val):
    """Best-effort float, tolerating strings with thousands separators."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def gst_amount(taxes: dict | None) -> float:
    """The bill's tax total: what the document printed, or the parts added up.

    Both callers used to add the components unconditionally, which quietly lost
    the tax on every invoice that prints one combined "GST @ 18% ... 864.22"
    line and no CGST/SGST split — there was nowhere for that number to live, so
    the bill posted with zero tax. The schema now has somewhere (see the GST
    rules above: the model copies the printed total and does not invent a
    split), and this is the one place that decides which to use.

    The printed total wins when there is one. It is a single number a human
    typeset deliberately, rather than three the extractor had to find and read
    separately — and the bill detail's `reconciles` flag surfaces the
    disagreement when both exist and differ, rather than hiding it inside an
    arithmetic choice made here.
    """
    taxes = taxes or {}
    stated = _to_num(taxes.get("total_tax_amount"))
    if stated is not None and stated > 0:
        return float(stated)
    return float(sum(
        _to_num(taxes.get(k)) or 0
        for k in ("cgst_amount", "sgst_amount", "igst_amount", "cess_amount")
    ))


def _rel_close(x: float, y: float, tol: float = 0.01) -> bool:
    """True if x and y agree within `tol` relative error (default 1%)."""
    scale = max(abs(x), abs(y), 1e-9)
    return abs(x - y) / scale <= tol


def _reconcile_line_amounts(data: dict, trust_printed_amounts: bool = False) -> None:
    """Cross-check each line's (quantity, rate, amount) and repair OCR digit slips.

    `trust_printed_amounts=True` (the text-layer path) short-circuits the whole
    repair: those numbers were read from the PDF's character stream, not inferred
    from pixels, so a quantity × rate ≠ amount line is a real discount or unit
    mismatch rather than a misread — rewriting it would corrupt correct data.

    GPT-4o vision occasionally fat-fingers a single digit / decimal on a line —
    and it can be ANY of the three fields, not just the amount (e.g. ₹2,02,131.86
    read as ₹20,21,311.86, or a quantity read with an extra digit). We therefore
    don't blindly trust Qty × Rate. Instead:

      1. If Qty × Rate already matches the printed amount (within 1%), the line is
         self-consistent — keep it as-is.
      2. If they disagree, one of the values is misread. We can't tell which from
         the line alone, so we let the invoice SUBTOTAL arbitrate: for each
         inconsistent line we choose between the OCR amount and Qty × Rate,
         picking the combination that makes all line amounts sum closest to the
         printed subtotal. This naturally corrects a bad amount (Qty × Rate wins)
         OR a bad qty/rate (the OCR amount wins), whichever reconciles the bill.
      3. With no subtotal to anchor against, fall back to Qty × Rate (the ingest
         convention) so the review UI still matches what gets stored.

    Mirrors the intent of ingest._insert_line_items so the review screen and the
    stored value always agree.
    """
    items = [li for li in (data.get("line_items") or []) if isinstance(li, dict)]
    if not items:
        return

    def _product(li: dict):
        """The line's own arithmetic: qty × rate, or qty × rate / 100 when the
        rate is a percentage. Used everywhere below instead of a bare product, or
        a percent line gets 'corrected' to 100× its real value."""
        q, r = _to_num(li.get("quantity")), _to_num(li.get("rate"))
        if q is None or r is None:
            return None
        return round(q * r / 100, 2) if li.get("rate_is_percent") else round(q * r, 2)

    if trust_printed_amounts:
        # Printed amounts stand, but an inconsistent triple in the text path means a
        # MIS-PARSE (a wrapped or comma-grouped number split into the wrong columns),
        # not a discount — and ingest recomputes amount as qty × rate, so a silent one
        # would land a wrong line total. Surface it instead of rewriting the data.
        for li in items:
            q, r, a = _to_num(li.get("quantity")), _to_num(li.get("rate")), _to_num(li.get("amount"))
            prod = _product(li)
            if prod is not None and a and not _rel_close(prod, a):
                logger.warning(
                    "[ocr] line arithmetic mismatch: qty=%s × rate=%s%s = %s but amount=%s (%r) "
                    "— check for a wrapped/comma-grouped number read into the wrong column",
                    q, r, "%" if li.get("rate_is_percent") else "", prod, a,
                    str(li.get("description"))[:60],
                )
        return

    parsed = []
    for li in items:
        a = _to_num(li.get("amount"))
        p = _product(li)
        parsed.append({"li": li, "a": a, "p": p, "chosen": None})

    # Pass 1 — resolve everything we can decide from the line alone.
    for x in parsed:
        a, p = x["a"], x["p"]
        if p is None:
            x["chosen"] = a                 # only the OCR amount is available
        elif a is None:
            x["chosen"] = p                 # only Qty × Rate is available
        elif _rel_close(p, a):
            x["chosen"] = a                 # self-consistent, keep OCR amount

    # Pass 2 — inconsistent lines: let the subtotal choose amount vs Qty × Rate.
    undecided = [x for x in parsed if x["chosen"] is None]
    if undecided:
        subtotal = _to_num(data.get("subtotal"))
        if subtotal is None:
            subtotal = _to_num((data.get("taxes") or {}).get("taxable_amount"))

        if subtotal is not None and len(undecided) <= 16:
            fixed = sum(x["chosen"] for x in parsed if x["chosen"] is not None)
            best_mask, best_err = 0, None
            for mask in range(1 << len(undecided)):
                total = fixed
                for i, x in enumerate(undecided):
                    total += x["a"] if (mask >> i) & 1 else x["p"]
                err = abs(total - subtotal)
                if best_err is None or err < best_err:
                    best_err, best_mask = err, mask
            for i, x in enumerate(undecided):
                x["chosen"] = x["a"] if (best_mask >> i) & 1 else x["p"]
        else:
            for x in undecided:             # no anchor → prefer Qty × Rate
                x["chosen"] = x["p"] if x["p"] is not None else x["a"]

    for x in parsed:
        if x["chosen"] is not None:
            x["li"]["amount"] = x["chosen"]


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_any_date(raw: str) -> date | None:
    """Parse the invoice/billing date across the formats the OCR emits."""
    from datetime import datetime
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d",
                "%d-%b-%Y", "%d/%b/%Y", "%d %b %Y", "%d %B %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _prev_month(d: date) -> str:
    """Canonical YYYY-MM of the month BEFORE date d."""
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return f"{y:04d}-{m:02d}"


def _normalize_service_month(raw: str, invoice_date: date | None) -> str | None:
    """Normalise a raw service-month string to canonical YYYY-MM, or None.

    Handles ISO, month-name+year, numeric MM/YYYY, and date ranges (start month).
    A bare month name gets its year inferred from the invoice date (invoices bill
    past usage, so a month after the invoice month rolls back a year). Returns
    None when nothing month-like is present — the caller applies the fallback."""
    s = (raw or "").strip()
    if not s:
        return None

    # Range → keep only the first (start) token. Split ONLY on clear range
    # separators (to / through / – / — / a SPACED hyphen), never a bare hyphen
    # inside a date like "01-Feb-2026".
    first = re.split(r"\s+to\s+|\s+through\s+|\s+thru\s+|\s*[–—]\s*|\s+-\s+",
                     s, maxsplit=1, flags=re.I)[0]

    # ISO / numeric YYYY-MM
    m = re.search(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])\b", first)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    # numeric MM/YYYY or MM-YYYY
    m = re.search(r"\b(0?[1-9]|1[0-2])[-/.](20\d{2})\b", first)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    # numeric within a range like 01/02/2026 (DD/MM/YYYY — Indian) → take MM,YYYY
    m = re.search(r"\b\d{1,2}[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})\b", first)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    # month name (+ optional year), with fuzzy match on the first 3+ letters
    m = re.search(r"([A-Za-z]{3,9})[\s\-'/.]*((?:20)?\d{2})?", first)
    if m:
        name = m.group(1).lower()[:4]
        mon = _MONTHS.get(name) or _MONTHS.get(name[:3])
        if mon:
            yr_raw = m.group(2)
            if yr_raw:
                yr = int(yr_raw)
                yr = yr + 2000 if yr < 100 else yr
            elif invoice_date:
                yr = invoice_date.year
                # invoices bill past usage — a month after the invoice month is last year
                if mon > invoice_date.month:
                    yr -= 1
            else:
                return None
            return f"{yr:04d}-{mon:02d}"
    return None


def _apply_service_month(data: dict) -> None:
    """Normalise bill-level and per-line service_month in place, and apply the
    fallback: if NO service month is present anywhere on the bill, use the month
    BEFORE the billing (invoice) date, since invoices bill the prior period."""
    inv_date = _parse_any_date(str(data.get("invoice_date") or ""))

    for li in (data.get("line_items") or []):
        if isinstance(li, dict) and li.get("service_month"):
            li["service_month"] = _normalize_service_month(str(li["service_month"]), inv_date) or ""

    bill_sm = _normalize_service_month(str(data.get("service_month") or ""), inv_date)

    if not bill_sm:
        # Fall back to the most common line-item month, else previous month of billing date.
        line_months = [li["service_month"] for li in (data.get("line_items") or [])
                       if isinstance(li, dict) and li.get("service_month")]
        if line_months:
            bill_sm = max(set(line_months), key=line_months.count)
        elif inv_date:
            bill_sm = _prev_month(inv_date)

    data["service_month"] = bill_sm or ""


class PdfTooLongError(ValueError):
    """A PDF over config.MAX_PDF_PAGES. The message is written to be shown to a user."""


def _too_long_message(n: int, filename: str) -> str:
    return (f"{filename or 'This PDF'} has {n} pages — the limit is {config.MAX_PDF_PAGES}. "
            "Split it and upload the pages you need.")


def check_pdf_pages(data: bytes, filename: str = "") -> None:
    """Raise PdfTooLongError when `data` is a PDF with more than config.MAX_PDF_PAGES pages.

    Anything that is not a readable PDF passes: this guards length only, and the
    caller's own handling decides what to do with a non-PDF or a broken one.
    """
    if b"%PDF" not in data[:1024]:
        return
    import fitz  # pymupdf
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            n = doc.page_count
    except Exception:
        return
    if n > config.MAX_PDF_PAGES:
        raise PdfTooLongError(_too_long_message(n, filename))


def invoice_upload_id(filename: str) -> str:
    """Deterministic UUID v5 for a filename — idempotent re-uploads."""
    return str(uuid.uuid5(_UUID_NS, filename))


def _pdf_to_images(pdf_path: str, dpi: int, max_pages: int):
    """Yield up to `max_pages` rendered pages as PIL Images using pymupdf.

    A generator, not a list: an A4 page at 200 dpi is ~12 MB of RGB, so holding a
    whole document froze the 1 GB API box on 2026-10-10 (an 88-page book ~ 1 GB).
    """
    import fitz  # pymupdf
    from PIL import Image
    doc   = fitz.open(pdf_path)
    scale = dpi / 72.0
    mat   = fitz.Matrix(scale, scale)
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def _pdf_to_text(pdf_path: str) -> list[str]:
    """Extract each page's embedded text layer, preserving column spacing.

    sort=True lays glyphs out in reading order with the original whitespace, which
    keeps invoice tables column-aligned — pymupdf's find_tables() collapses these
    layouts into one merged cell, so plain sorted text is the more faithful option.
    """
    import fitz  # pymupdf
    doc = fitz.open(pdf_path)
    try:
        return [page.get_text("text", sort=True) for page in doc]
    finally:
        doc.close()


def _text_layer_ok(pages: list[str]) -> bool:
    """True when the text layer is rich enough to extract from without vision.

    Requires both a healthy average AND no near-empty page, so a scanned page
    inside an otherwise-digital PDF sends the whole document to vision rather
    than silently contributing nothing.
    """
    if not pages:
        return False
    counts = [len(t.strip()) for t in pages]
    mean = sum(counts) / len(counts)
    return (mean >= config.OCR_TEXT_MIN_CHARS_PER_PAGE
            and min(counts) >= config.OCR_TEXT_MIN_CHARS_ANY_PAGE)


def _has_raster_images(pdf_path: str) -> bool:
    """True when any page embeds a raster image.

    A letterhead logo band is a raster, and anything printed inside it (commonly the
    vendor's GSTIN and address) is invisible to text extraction. Its presence is what
    makes a vision re-read worth paying for; a fully vector/text PDF has no hidden
    content, so there is nothing for vision to add.
    """
    import fitz  # pymupdf
    doc = fitz.open(pdf_path)
    try:
        return any(page.get_images(full=True) for page in doc)
    finally:
        doc.close()


# Fields a GST tax invoice must carry. An empty one after a successful text read is
# the signal that the value lives in a graphic rather than that it does not exist.
_CRITICAL_FIELDS = (
    ("vendor", "gstin"),
    ("vendor", "name"),
    ("invoice_number",),
    ("total_amount",),
)


def _is_blank(val) -> bool:
    return val is None or (isinstance(val, str) and not val.strip())


def _missing_critical(data: dict) -> list[str]:
    """Names of the critical fields the extraction left blank."""
    missing = []
    for path in _CRITICAL_FIELDS:
        cur = data
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if _is_blank(cur):
            missing.append(".".join(path))
    return missing


def _fill_gaps(base: dict, extra: dict) -> list[str]:
    """Fill blank fields in `base` from `extra`; never overwrite a value base already has.

    Text-layer values are character-exact, so they always win. Vision only supplies
    what the text layer could not see. Lists are copied only when base's is empty —
    merging two differently-segmented line-item lists would corrupt both.
    """
    filled = []
    for key, val in (extra or {}).items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            filled += [f"{key}.{k}" for k in _fill_gaps(base[key], val)]
        elif isinstance(val, list):
            if not base.get(key) and val:
                base[key] = val
                filled.append(key)
        elif _is_blank(base.get(key)) and not _is_blank(val):
            base[key] = val
            filled.append(key)
    return filled


def _crop_raster_regions(pdf_path: str, dpi: int = 300, max_long: int = 2000,
                         max_crops: int = 8) -> list:
    """Render each embedded raster region as its own high-resolution image.

    The fields a text layer misses sit inside letterhead graphics, and those bands are
    small (a 503x63pt strip here). Downscaling a whole page to 1024px on the short side
    renders that print illegible — GPT-4o misread this bill's GSTIN as 09AAAFV7056G1Z9
    instead of 09AMWPV0580G1ZG. Cropping to the region and rendering it at `dpi`
    instead makes the same text several times larger for a fraction of the pixels.
    """
    import fitz  # pymupdf
    from PIL import Image
    doc = fitz.open(pdf_path)
    crops = []
    try:
        for page in doc:
            seen = set()
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                seen.add(xref)
                for rect in page.get_image_rects(xref):
                    # Pad slightly: printed text often sits just outside the image box.
                    clip = (rect + (-4, -4, 4, 4)) & page.rect
                    if clip.width < 8 or clip.height < 8:
                        continue
                    scale = dpi / 72.0
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale),
                                          clip=clip, colorspace=fitz.csRGB)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    # Cap the LONG side: a wide, short banner normalised on its short
                    # side would balloon to an absurd width.
                    if max(img.size) > max_long:
                        f = max_long / max(img.size)
                        img = img.resize((max(1, int(img.width * f)),
                                          max(1, int(img.height * f))))
                    crops.append(img)
                    if len(crops) >= max_crops:
                        return crops
    finally:
        doc.close()
    return crops


def _encode_raw(img) -> str:
    """base64-encode an image as PNG without the short-side normalisation."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


# Asking for the whole invoice schema from a letterhead crop invites refusals — those
# bands are dense with third-party dealer logos, and the model sometimes declines and
# returns no content at all. This prompt narrows the task to transcribing the seller's
# own printed block and tells it to ignore brand marks outright.
BACKFILL_SYSTEM_PROMPT = """You are transcribing text from cropped regions of an Indian GST tax invoice —
letterhead, stamp and signature bands. These crops contain the SELLER's own printed
registration and contact details. Return ONLY valid JSON:

{
  "vendor": { "name":"","address":"","gstin":"","pan":"","phone":"","email":"" },
  "invoice_number": "",
  "total_amount": null
}

Rules:
- Transcribe only what you can literally read, character by character.
- GSTIN: exactly 15 characters, copied verbatim. Read each character carefully and do
  NOT guess a digit — an unreadable GSTIN must be "" rather than an approximation.
- Third-party dealer and brand logos often appear in these bands. Ignore them
  completely: do not identify, describe, or transcribe brand marks or trademarks. Only
  the seller's own name/address/GSTIN/PAN/phone/email block is wanted.
- Any field you cannot read → "" (or null for total_amount). Never invent a value.
- Return ONLY the JSON object — no markdown, no prose."""


async def _call_vision_backfill(b64_pages: list[str], filename: str) -> dict:
    """Focused transcription call over cropped graphic regions."""
    content: list[dict] = [
        {"type": "text",
         "text": f"Document: {filename}. Transcribe the seller's printed details."},
    ]
    for b64 in b64_pages:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })
    resp = await _get_client().chat.completions.create(
        model=config.OPENAI_OCR_MODEL,
        messages=[
            {"role": "system", "content": BACKFILL_SYSTEM_PROMPT},
            {"role": "user",   "content": content},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=1024,
    )
    return json.loads(_content_or_raise(resp, "vision backfill"))


async def _vision_backfill(path: Path, dpi: int, missing: list[str]) -> dict:
    """Re-read only the graphic regions, to recover fields the text layer cannot see.

    Retries once: refusals on logo-dense crops are intermittent, so a lone attempt
    leaves the field blank roughly a third of the time. Falls back to the full-page
    render when the PDF has no croppable raster region.
    """
    crops = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _crop_raster_regions(str(path)))
    if not crops:
        return await _vision_from_pdf(path, dpi)
    b64 = [_encode_raw(c) for c in crops]
    logger.info("[ocr] %s: reading %d cropped graphic region(s) for %s",
                path.name, len(b64), missing)
    for attempt in (1, 2):
        try:
            return await _call_vision_backfill(b64, path.name)
        except ValueError as exc:          # no content / refusal — transient
            if attempt == 2:
                raise
            logger.info("[ocr] %s: backfill attempt %d returned nothing (%s) — retrying",
                        path.name, attempt, exc)
    return {}


async def _vision_from_pdf(path: Path, dpi: int) -> dict:
    """Rasterise pages and run the vision extraction."""
    loop  = asyncio.get_event_loop()
    # Encode as each page renders so only one full-size page is held at a time.
    b64_pages = await loop.run_in_executor(
        None, lambda: [_encode_page(pg) for pg in _pdf_to_images(str(path), dpi, max_pages=20)])
    return await _call_vision(b64_pages, path.name)


async def process_pdf_async(pdf_path: str, dpi: int = 200) -> dict:
    """PDF → text layer or page images → GPT-4o → structured JSON.

    Route chosen by config.OCR_TO_USE ("auto" | "text" | "vision"); the mode that
    actually ran is reported back as `_ocr_mode` so a caller can tell which of the
    two paths produced a given result.
    """
    path = Path(pdf_path).resolve()
    mode = config.OCR_TO_USE
    loop = asyncio.get_event_loop()

    import fitz  # pymupdf
    try:
        with fitz.open(str(path)) as doc:
            n_pages = doc.page_count
    except Exception:
        n_pages = 0   # unreadable: let the paths below report it as they always have
    if n_pages > config.MAX_PDF_PAGES:
        raise PdfTooLongError(_too_long_message(n_pages, path.name))

    if mode not in ("auto", "text", "vision"):
        logger.warning("[ocr] unknown OCR_TO_USE=%r — falling back to 'auto'", mode)
        mode = "auto"

    used = "vision"
    if mode in ("auto", "text"):
        try:
            pages_text = await loop.run_in_executor(None, lambda: _pdf_to_text(str(path)))
        except Exception as exc:
            # Encrypted / malformed PDF: the text layer is unreadable but pymupdf can
            # often still rasterise it, so don't let this sink an otherwise fine doc.
            if mode == "text":
                raise
            logger.warning("[ocr] %s: text-layer read failed (%s) — using vision",
                           path.name, exc)
            pages_text = []
        if _text_layer_ok(pages_text):
            used   = "text"
            result = await _call_text(pages_text[:20], path.name)
            # Partially-selectable PDF: the text layer read fine but a legally-required
            # field is blank because it is baked into a raster letterhead. Re-read with
            # vision and fill only those holes, leaving exact text values untouched.
            missing = _missing_critical(result) if config.OCR_GAP_BACKFILL else []
            if missing and _has_raster_images(str(path)):
                logger.info("[ocr] %s: text layer missing %s — backfilling from vision",
                            path.name, missing)
                try:
                    filled = _fill_gaps(result, await _vision_backfill(path, dpi, missing))
                    if filled:
                        used = "text+vision"
                        logger.info("[ocr] %s: vision backfilled %s", path.name, filled)
                except Exception as exc:
                    # The text result is still usable — a failed backfill must not
                    # discard a good extraction.
                    logger.warning("[ocr] %s: vision backfill failed (%s)", path.name, exc)
        elif mode == "text":
            raise ValueError(
                f"OCR_TO_USE=text but {path.name} has no usable text layer "
                f"(chars/page={[len(t.strip()) for t in pages_text]}); "
                f"it is most likely a scan — use OCR_TO_USE=auto"
            )
        else:
            if pages_text:
                logger.info("[ocr] %s: text layer too sparse (chars/page=%s) — using vision",
                            path.name, [len(t.strip()) for t in pages_text])
            result = await _vision_from_pdf(path, dpi)
    else:
        result = await _vision_from_pdf(path, dpi)

    result["_ocr_mode"]    = used
    result["_source_file"] = path.name
    result["_upload_id"]   = invoice_upload_id(path.name)
    return result


async def process_image_async(image_path: str) -> dict:
    """Single image (PNG/JPG/TIFF) → GPT-4o vision → structured JSON.

    An image has no text layer to read, so this path ignores OCR_TO_USE and always
    uses vision.
    """
    from PIL import Image
    path  = Path(image_path).resolve()
    loop  = asyncio.get_event_loop()
    img   = await loop.run_in_executor(None, lambda: Image.open(str(path)))
    b64   = _encode_page(img)
    result = await _call_vision([b64], path.name)
    result["_ocr_mode"]    = "vision"
    result["_source_file"] = path.name
    result["_upload_id"]   = invoice_upload_id(path.name)
    return result


def process_pdf(pdf_path: str, dpi: int = 200) -> dict:
    """Sync wrapper — for non-async callers (FastAPI background tasks, CLI)."""
    return asyncio.run(process_pdf_async(pdf_path, dpi))
