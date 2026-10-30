"""What an uploaded vendor document is, decided from what it says.

The "Add a document" dialog sends the file here before uploading it and gets
back one of:

  contract  a contract, agreement, IO / insertion order, work order or SOW:
            filed under Contracts & IOs, where its terms are read and checked
  existing  one of the types the team has already named ("Rate cards" …)
  new       something else, with a suggested name for a new type
  bill      an invoice: bills aren't filed here, they go through bill upload

Text comes from the file itself (PDF text layer, spreadsheet cells, Word text).
TypeSafe's Jev decides from that text: one call asks what kind of document it
is (every option explained: what it covers, what it doesn't, examples) and,
for something new, which name fits it. Jev answers with probabilities, so a
low-confidence call is flagged for the user to confirm. A scan or photo with
no text is looked at as an image by gpt-4o-mini instead; with neither, or if
they fail, a keyword guess stands in. Nothing is stored.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import zipfile

from fastapi import APIRouter, HTTPException, Request

import httpx

from ..config import JEV_API_KEY, OPENAI_API_KEY, get_conn, put_conn
from ..security.auth import require_tenant

log = logging.getLogger(__name__)
router = APIRouter()

_MAX_BYTES = 25_000_000
_TEXT_CHARS = 6000
_KINDS = ("contract", "existing", "new", "bill")


# ── Getting the words out ────────────────────────────────────────────────────

def extract(file_name: str, data: bytes) -> tuple[str, bytes | None]:
    """(text, image): the document's text, and a PNG of its first page when it has
    little or no text (a scan or a photo) so it can be looked at instead."""
    name = (file_name or "").lower()
    try:
        if data[:4] == b"%PDF" or name.endswith(".pdf"):
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            try:
                text = "\n".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
                image = None
                if len(text.strip()) < 80 and doc.page_count:
                    image = doc[0].get_pixmap(dpi=100).tobytes("png")
            finally:
                doc.close()
            return text[:_TEXT_CHARS], image
        if name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")):
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((1400, 1400))
            out = io.BytesIO()
            img.save(out, "PNG")
            return "", out.getvalue()
        if name.endswith((".xlsx", ".xlsm")):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets[:3]:
                lines.append(f"[{ws.title}]")
                for row in ws.iter_rows(max_row=40, values_only=True):
                    cells = [str(c) for c in row if c not in (None, "")]
                    if cells:
                        lines.append(" | ".join(cells))
            return "\n".join(lines)[:_TEXT_CHARS], None
        if name.endswith(".docx"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf8", "ignore")
            return re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))[:_TEXT_CHARS], None
        if name.endswith((".csv", ".txt", ".md", ".json")):
            return data[:_TEXT_CHARS * 2].decode("utf8", "ignore")[:_TEXT_CHARS], None
    except Exception as e:  # an unreadable file is still uploadable; it just isn't read
        log.info("couldn't read %s: %s", file_name, e)
    return "", None


# ── Deciding ─────────────────────────────────────────────────────────────────

_BILL = re.compile(r"\b(tax\s+invoice|invoice\s+(no|number|#)|bill\s+of\s+supply|irn\b|e-?invoice)", re.I)
_CONTRACT = re.compile(r"\b(agreement|contract|insertion\s+order|\bIO\b|work\s+order|statement\s+of\s+work|"
                       r"\bSOW\b|memorandum\s+of\s+understanding|\bMOU\b|terms\s+and\s+conditions|master\s+service)", re.I)


def _title(s: str) -> str:
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", s, flags=re.I)
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\b\d[\d\s/.-]*\b", " ", s)
    s = " ".join(s.split())[:40]
    return s[:1].upper() + s[1:] if s else ""


def guess(file_name: str, text: str, types: list[str]) -> dict:
    """The keyword stand-in, when the model isn't there to ask."""
    hay = f"{file_name}\n{text[:3000]}"
    low = hay.lower()
    for t in types:
        if t.lower() in low or t.lower().rstrip("s") in low:
            return {"kind": "existing", "label": t, "reason": f"Mentions “{t}”."}
    if _CONTRACT.search(hay):
        return {"kind": "contract", "reason": "Reads like a contract or IO."}
    if _BILL.search(hay):
        return {"kind": "bill", "reason": "Reads like an invoice."}
    return {"kind": "new", "suggested_name": _title(file_name) or "Other documents", "reason": ""}


_PROMPT = """You file documents a vendor ("{vendor}") sent to a company's finance team.
Decide what this document is:
- "contract": a contract, agreement, MSA, insertion order (IO), release order,
  work order, statement of work, or MOU — anything setting terms with this vendor.
- "bill": a tax invoice, bill of supply, proforma invoice or debit note asking to be paid.
- "existing": it belongs to one of the team's own types: {types}.
  Use this only when it really is that kind of document.
- "new": anything else (certificates, rate cards, KYC papers, letters, reports…).
  Give it a short type name a finance team would file it under, plural where natural,
  e.g. "MSME certificates", "Rate cards", "Cancelled cheques", "GST certificates".
File name: {file_name}
Reply as JSON: {{"kind": "...", "label": "<the existing type, exactly as written, if kind is existing>",
"suggested_name": "<type name, if kind is new>", "title": "<what this document is, 3-8 words>",
"reason": "<one short sentence a user would find useful, e.g. who it's between and the date>"}}"""


def ask_model(vendor: str, file_name: str, text: str, image: bytes | None, types: list[str]) -> dict | None:
    if not OPENAI_API_KEY or (not text.strip() and not image):
        return None
    from openai import OpenAI
    content: list[dict] = [{"type": "text", "text": _PROMPT.format(
        vendor=vendor or "the vendor", file_name=file_name,
        types=", ".join(f"“{t}”" for t in types[:30]) or "(none yet)")}]
    if text.strip():
        content.append({"type": "text", "text": f"Document text:\n{text}"})
    if image:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image).decode(), "detail": "low"}})
    resp = OpenAI(api_key=OPENAI_API_KEY, timeout=25).chat.completions.create(
        model="gpt-4o-mini", temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": content}],
    )
    return json.loads(resp.choices[0].message.content or "{}")


# ── Jev ──────────────────────────────────────────────────────────────────────

_JEV_URL = "https://api.typesafe.ai/v1/systemone"
_JEV_MODEL = "jev-latest"
# Below this, the answer is shown but the user is asked to confirm it.
SURE = 0.5

# Option keys for the kind question. Existing types are offered as "type: <name>".
_K_CONTRACT, _K_BILL, _K_NEW = "Contract or IO", "Bill or invoice", "Something else"

_KIND_CRITERIA = {
    _K_CONTRACT: {
        "what": "A document that sets the terms of business between the buyer and this vendor: a contract, "
                "agreement, master service agreement (MSA), insertion order (IO) or release order for "
                "advertising, work order, purchase order, statement of work (SOW), MOU, NDA, addendum or "
                "renewal. It usually names both parties and states rates, scope, payment terms or a term.",
        "not_for": "An invoice or bill asking for payment (that is a bill), a rate card or price list that "
                   "only lists prices without being signed or agreed by both sides, or a certificate.",
        "examples": ["Insertion Order No. IO/2026/114 between Acme and Signzy, campaign period Oct-Dec",
                     "Master Services Agreement, payment terms 45 days from invoice",
                     "Work order for annual maintenance, signed by both parties"],
    },
    _K_BILL: {
        "what": "A document asking the buyer to pay: a tax invoice, bill of supply, proforma invoice, debit "
                "note or e-invoice. It has an invoice number, invoice date, amounts with GST, and usually an "
                "IRN or QR code.",
        "not_for": "A contract that only mentions how invoices will be raised, a statement listing many "
                   "invoices, or a payment receipt.",
        "examples": ["TAX INVOICE  Invoice No: SGL/26-27/0445  Taxable value 63,390  IGST 18%",
                     "Proforma invoice for October media spend"],
    },
    _K_NEW: {
        "what": "Any other vendor document that is neither a contract/IO nor a bill, and doesn't fit one of "
                "the team's existing types: for example a certificate (MSME/Udyam, GST registration, PAN, "
                "incorporation), a cancelled cheque or bank letter, a rate card, a statement of account, a "
                "KYC document, a letter, a report or a presentation.",
        "not_for": "Contracts, IOs, invoices, or a document that matches one of the team's existing types.",
    },
}

# Names offered for something new: the common kinds of vendor paperwork.
_NAMES = {
    "MSME certificates": "Udyam / MSME registration certificates",
    "GST certificates": "GST registration certificates (Form GST REG-06)",
    "PAN cards": "Copies of the vendor's PAN card",
    "Cancelled cheques": "Cancelled cheques or bank account confirmation letters",
    "Incorporation documents": "Certificate of incorporation, MOA/AOA, partnership deed",
    "Rate cards": "Price lists and rate cards for the vendor's services",
    "Statements of account": "Ledger statements or statements of account listing many invoices",
    "Tax documents": "TDS certificates (Form 16A), lower deduction certificates, tax declarations",
    "KYC documents": "Identity and address proofs, KYC forms",
    "Credit notes": "Credit notes reducing an earlier invoice",
    "Payment receipts": "Receipts or acknowledgements confirming a payment was received",
    "Letters": "Letters, notices and other correspondence",
    "Reports": "Reports, presentations, proposals and decks",
    "Other documents": "Anything that fits none of these",
}


def jev_questions(types: list[str], examples: dict[str, list[str]]) -> dict:
    kinds: dict = dict(_KIND_CRITERIA)
    for t in types[:40]:
        kinds[f"type: {t}"] = {
            "what": f"The team's own document type “{t}”. Choose it when this document is one of those.",
            "not_for": "Contracts, IOs and bills, which have their own options.",
            **({"examples": [f"previously filed here: {n}" for n in examples.get(t, [])[:3]]} if examples.get(t) else {}),
        }
    return {
        "kind": {
            "type": "choice",
            "instructions": {
                "question": "What kind of document is this, so it can be filed in the right place for this vendor?",
                "focus": "Judge the document's main purpose from its text and file name, not every topic it "
                         "mentions. A contract that mentions invoices is still a contract; an invoice that "
                         "quotes a contract number is still a bill. Prefer one of the team's existing types "
                         "when the document really is one of them.",
            },
            "criteria": kinds,
        },
        "name": {
            "type": "choice",
            "instructions": {
                "question": "If this document were filed under a new type, which name fits it best?",
                "focus": "Pick the name a finance team would file this document under.",
            },
            "criteria": _NAMES,
        },
    }


def ask_jev(vendor: str, file_name: str, text: str, types: list[str], examples: dict[str, list[str]]) -> dict | None:
    """Jev's call on the text, in the same shape as ask_model's, plus its confidence."""
    if not JEV_API_KEY or not text.strip():
        return None
    r = httpx.post(_JEV_URL, timeout=20, headers={"Authorization": f"Bearer {JEV_API_KEY}"}, json={
        "model": _JEV_MODEL,
        "state": {"vendor": vendor, "file_name": file_name, "text": text},
        "questions": jev_questions(types, examples),
    })
    r.raise_for_status()
    return read_jev(r.json())


def read_jev(body: dict) -> dict | None:
    answers = body.get("answers") or {}
    kind, name = answers.get("kind") or {}, answers.get("name") or {}
    pick = kind.get("choice")
    if not pick:
        return None
    conf = float(kind.get("confidence") or 0)
    out: dict = {"confidence": round(conf, 3)}
    if pick == _K_CONTRACT:
        out.update(kind="contract", reason="Reads as a contract or IO.")
    elif pick == _K_BILL:
        out.update(kind="bill", reason="Reads as an invoice.")
    elif pick.startswith("type: "):
        out.update(kind="existing", label=pick[len("type: "):], reason="Matches one of your types.")
    else:
        suggestion = name.get("choice")
        out.update(kind="new", suggested_name="" if suggestion in (None, "Other documents") else suggestion, reason="")
    return out


def _type_examples(tenant: str) -> dict[str, list[str]]:
    """A few file names already filed under each type: real examples for Jev."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT doc_label, (array_agg(file_name ORDER BY uploaded_at DESC NULLS LAST))[1:3]
                  FROM vendor_documents
                 WHERE tenant_id = %s AND doc_type = 'other' AND doc_label IS NOT NULL AND deleted_at IS NULL
                 GROUP BY doc_label
            """, (tenant,))
            return {r[0]: [x for x in (r[1] or []) if x] for r in cur.fetchall()}
    finally:
        put_conn(conn)


def settle(raw: dict | None, types: list[str], fallback: dict) -> dict:
    """A clean answer the dialog can trust, whatever the model said."""
    r = raw or {}
    kind = r.get("kind") if r.get("kind") in _KINDS else None
    out = {"kind": kind or fallback["kind"], "title": str(r.get("title") or "")[:80],
           "reason": str(r.get("reason") or fallback.get("reason") or "")[:200], "source": "ai" if kind else "guess"}
    if kind and isinstance(r.get("confidence"), (int, float)):
        out["confidence"] = float(r["confidence"])
        out["sure"] = out["confidence"] >= SURE
    if out["kind"] == "existing":
        want = str(r.get("label") or fallback.get("label") or "").strip().lower()
        match = next((t for t in types if t.lower() == want), None)
        if match:
            out["label"] = match
        else:                                  # a type that doesn't exist is a new one
            out["kind"] = "new"
            out["suggested_name"] = str(r.get("label") or "").strip()[:80]
    if out["kind"] == "new":
        name = " ".join(str(out.get("suggested_name") or r.get("suggested_name") or fallback.get("suggested_name") or "").split())[:80]
        # A suggestion that matches a type the team already has goes there.
        match = next((t for t in types if t.lower() == name.lower()), None)
        if match:
            out = {**out, "kind": "existing", "label": match}
        else:
            out["suggested_name"] = name
    return out


def _types(tenant: str) -> list[str]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT doc_label FROM vendor_documents
                 WHERE tenant_id = %s AND doc_type = 'other' AND doc_label IS NOT NULL AND deleted_at IS NULL
                 GROUP BY doc_label ORDER BY COUNT(*) DESC, doc_label LIMIT 50
            """, (tenant,))
            return [r[0] for r in cur.fetchall()]
    finally:
        put_conn(conn)


def _vendor_name(tenant: str, supplier_id: str) -> str | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM dim_suppliers WHERE supplier_id::text = %s AND tenant_id = %s", (supplier_id, tenant))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        put_conn(conn)


@router.post("/api/vendors/{supplier_id}/documents/classify")
async def classify_vendor_document(supplier_id: str, request: Request):
    """What this file is, before it's uploaded. Nothing is stored."""
    tenant = require_tenant()
    async with request.form() as form:
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(status_code=422, detail="file field is required")
        file_name = file.filename or "document"
        data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="That file is too big to read.")
    vendor = _vendor_name(tenant, supplier_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    types = _types(tenant)
    text, image = extract(file_name, data)
    fallback = guess(file_name, text, types)
    raw, engine = None, "keywords"
    if text.strip() and JEV_API_KEY:
        try:
            raw, engine = ask_jev(vendor, file_name, text, types, _type_examples(tenant)), "jev"
        except Exception as e:
            log.info("Jev couldn't classify %s: %s", file_name, e)
    if raw is None:
        # No text to give Jev (a scan or photo), or Jev failed: look at it instead.
        try:
            raw, engine = ask_model(vendor, file_name, text, image, types), "vision"
        except Exception as e:
            log.info("document classify fell back to keywords: %s", e)
    out = settle(raw, types, fallback)
    out["engine"] = engine if out["source"] == "ai" else "keywords"
    return out
