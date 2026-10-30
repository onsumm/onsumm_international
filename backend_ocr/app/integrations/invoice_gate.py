"""Is this upload an invoice at all? Asked before extraction, so a selfie, a chat
screenshot or a contract is turned away instead of being read as a bill.

A PDF with a usable text layer is judged by TypeSafe's Jev from its text. A scan
or a photo has no text, so OpenAI's Decisions API looks at its first page.

Only a confident "no" rejects. If the check can't run (no key, timeout, model
error) or isn't sure, the file goes on to extraction as before: a real bill must
never be lost to the gate.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from ..config import JEV_API_KEY, OPENAI_API_KEY
from .ocr import _encode_page, _pdf_to_text, _text_layer_ok
from utils.ocr_engine import _pdf_to_images

log = logging.getLogger(__name__)

_JEV_URL = "https://api.typesafe.ai/v1/systemone"
_JEV_MODEL = "jev-latest"
_DECISIONS_URL = "https://api.openai.com/v1/decisions"
_VISION_MODEL = "gpt-6-luna"
_TEXT_CHARS = 6000
# A "not an invoice" below this goes through: extraction and review will show it.
REJECT_AT = 0.8

_K_YES, _K_NO = "Invoice or bill", "Not an invoice"

_CRITERIA = {
    _K_YES: {
        "what": "A document a finance team would book or pay: a tax invoice, bill of supply, proforma "
                "invoice, e-invoice, debit or credit note, or a receipt for a purchase. It has a seller, "
                "a document number or date, and amounts.",
        "not_for": "A document that only mentions invoices without being one.",
        "examples": ["TAX INVOICE  Invoice No: SGL/26-27/0445  Taxable value 63,390  IGST 18%",
                     "Proforma invoice for October media spend",
                     "Restaurant bill, GSTIN 27AAACR..., Total 1,240"],
    },
    _K_NO: {
        "what": "Anything that is not an invoice or bill: a contract, agreement or purchase order, a "
                "statement of account, a certificate, an ID card, a cheque, a letter, a report or "
                "presentation, a resume, a menu, or text with nothing to do with a purchase.",
        "not_for": "Any kind of invoice, bill, debit/credit note or purchase receipt.",
    },
}

# The choices for a scan or photo. Only "invoice" goes on to extraction; the
# others say what the file was instead, which becomes the skip reason.
_VISION_CHOICES = [
    {"value": "invoice", "description": "A tax invoice, bill of supply, proforma invoice, e-invoice, debit or "
                                        "credit note, or a purchase receipt, including a skewed, blurry, "
                                        "partial or handwritten photo or scan of one."},
    {"value": "contract", "description": "A contract, agreement, purchase order, work order or insertion order."},
    {"value": "statement", "description": "A statement of account, ledger or bank statement listing many entries."},
    {"value": "id or certificate", "description": "An ID card, PAN or Aadhaar card, cheque, or a GST, MSME or "
                                                  "other registration certificate."},
    {"value": "photo or screenshot", "description": "A photo of a person, place or object, a selfie, a meme, or "
                                                    "a screenshot of a chat, app or website."},
    {"value": "blank", "description": "A blank, black or unreadable page."},
    {"value": "other document", "description": "Any other document that is not an invoice."},
]

class NotAnInvoice(Exception):
    """The upload confidently isn't an invoice; the message says what it looked like."""


def _ask_jev(file_name: str, text: str) -> dict | None:
    if not JEV_API_KEY:
        return None
    r = httpx.post(_JEV_URL, timeout=20, headers={"Authorization": f"Bearer {JEV_API_KEY}"}, json={
        "model": _JEV_MODEL,
        "state": {"file_name": file_name, "text": text[:_TEXT_CHARS]},
        "questions": {"kind": {
            "type": "choice",
            "instructions": {
                "question": "Is this document an invoice or bill that was uploaded to be paid or booked?",
                "focus": "Judge the document's main purpose, not every topic it mentions. A contract that "
                         "mentions invoices is not an invoice; an invoice that quotes a contract number is.",
            },
            "criteria": _CRITERIA,
        }},
    })
    r.raise_for_status()
    kind = (r.json().get("answers") or {}).get("kind") or {}
    if not kind.get("choice"):
        return None
    return {"is_invoice": kind["choice"] != _K_NO, "confidence": float(kind.get("confidence") or 0)}


async def _ask_vision(path: Path) -> dict | None:
    """OpenAI's Decisions API on page 1: a choice question, so the answer comes with
    a real probability per option rather than a confidence the model writes itself."""
    if not OPENAI_API_KEY:
        return None
    loop = asyncio.get_event_loop()
    # One page at low dpi: enough to tell a bill from a selfie, and cheap.
    b64 = await loop.run_in_executor(
        None, lambda: next((_encode_page(p, 512) for p in _pdf_to_images(str(path), 72, max_pages=1)), None))
    if not b64:
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(_DECISIONS_URL, headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, json={
            "model": _VISION_MODEL,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": f"Page 1 of a file uploaded to be processed as a bill: {path.name}"},
                {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            ]}],
            "questions": [{
                "type": "choice", "name": "kind", "choices": _VISION_CHOICES,
                "instructions": "What kind of document or image is this? Judge its main purpose: a photo of an "
                                "invoice is an invoice; a contract that mentions invoices is a contract.",
            }],
        })
    r.raise_for_status()
    kind = next((a for a in r.json().get("answers") or [] if a.get("name") == "kind"), None)
    if not kind or not kind.get("choice"):
        return None
    p_invoice = next((float(o.get("probability") or 0) for o in kind.get("probabilities") or []
                      if o.get("value") == "invoice"), 0.0)
    return {"is_invoice": kind["choice"] == "invoice", "confidence": 1 - p_invoice if kind["choice"] != "invoice" else p_invoice,
            "seen": "" if kind["choice"] in ("invoice", "other document") else kind["choice"]}


async def check_is_invoice(pdf_path: str) -> None:
    """Raise NotAnInvoice when the file confidently isn't one; otherwise return."""
    path = Path(pdf_path)
    loop = asyncio.get_event_loop()
    try:
        pages = await loop.run_in_executor(None, lambda: _pdf_to_text(str(path)))
    except Exception:
        pages = []
    try:
        if _text_layer_ok(pages):
            engine = "jev"
            verdict = await loop.run_in_executor(None, lambda: _ask_jev(path.name, "\n".join(pages[:3])))
        else:
            engine = "vision"
            verdict = await _ask_vision(path)
    except Exception as exc:
        log.warning("[invoice-gate] %s: check failed (%s) — letting it through", path.name, exc)
        return
    if not verdict:
        return
    log.info("[invoice-gate] %s: %s is_invoice=%s conf=%.2f", path.name, engine,
             verdict["is_invoice"], verdict["confidence"])
    if not verdict["is_invoice"] and verdict["confidence"] >= REJECT_AT:
        seen = verdict.get("seen")
        raise NotAnInvoice(f"Not an invoice{f' ({seen})' if seen else ''}, skipped")
