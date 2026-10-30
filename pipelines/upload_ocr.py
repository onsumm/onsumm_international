"""
Upload / OCR Pipeline
─────────────────────
Advances raw_invoice_uploads through its status machine:

    RECEIVED → PREPROCESSING → OCR_PRIMARY → OCR_FALLBACK? → VALIDATING → DONE
                                                                          ↓ (on error)
                                                                         ERROR

OCR engine:   GPT-4o vision (primary)  →  Sarvam OCR API (fallback, if key set)
File routing: PDF / PNG / JPG / TIFF   →  GPT-4o
              CSV / XLSX               →  structured file parser (no OCR needed)

Document routing at VALIDATING stage:
    document_type = invoice / receipt   →  stg_invoices  (AR, end-customer side)
    document_type = bill / purchase_order  →  stg_bills  (AP, supplier side)
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

import config
from db import get_pool
from models.enums import (
    InvoiceStatus, OcrEngine, UploadStatus, ValidationFlag,
)
from pipelines.base import BaseWorker
_audit = BaseWorker.log_audit

logger = logging.getLogger(__name__)


# ── Scalar helpers ─────────────────────────────────────────────────────────────

def _s(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _num(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _gstn_clean(raw: Any) -> str | None:
    """Validate and normalise a GSTIN to exactly 15 chars or return None."""
    if not raw:
        return None
    s = re.sub(r"\s+", "", str(raw)).upper()
    s = re.sub(r"^O", "0", s)   # common OCR confusion: O→0 at start
    return s if len(s) == 15 else None


_DATE_FORMATS = (
    "%d-%m-%Y", "%d/%m/%Y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d-%b-%Y", "%d/%b/%Y",
    "%d %b %Y", "%d %B %Y",
    "%B %d, %Y",
)


def _parse_ocr_date(val: Any) -> date | None:
    if not val:
        return None
    if isinstance(val, date):
        return val
    s = str(val).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _parse_amount(val: Any) -> Decimal:
    if not val:
        return Decimal("0")
    cleaned = re.sub(r"[₹$€£¥,\s]", "", str(val)).replace("Rs.", "").replace("USD", "").replace("EUR", "").replace("GBP", "").replace("JPY", "").strip()
    try:
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def _estimate_confidence(ocr: dict) -> float:
    """Score 0–1 based on how many key fields GPT-4o extracted."""
    score = 0.0
    if ocr.get("invoice_number"):           score += 0.30
    if ocr.get("invoice_date"):             score += 0.20
    if ocr.get("total_amount"):             score += 0.30
    if (ocr.get("vendor") or {}).get("name"): score += 0.20
    return min(score, 1.0)


def _is_bill(ocr: dict) -> bool:
    """True when GPT-4o identified the document as an AP bill/purchase order."""
    doc_type = (ocr.get("document_type") or "").lower()
    return doc_type in ("bill", "purchase_order", "po", "purchase invoice")


# ── OCR engines ────────────────────────────────────────────────────────────────

async def _set_ocr_jurisdiction(tenant_id: str) -> None:
    """Tell the OCR prompt which country's invoices this tenant receives
    (utils/ocr_engine.py; backend_ocr/app/core/jurisdiction.py). Indian tenants
    keep the GST prompt unchanged."""
    from backend_ocr.app.core.jurisdiction import build
    from utils.ocr_engine import set_ocr_jurisdiction
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM dim_tenants WHERE tenant_id = $1::uuid", tenant_id)
        j = build(dict(row) if row else None)
        set_ocr_jurisdiction(j.profile, j.country_code)
    except Exception:
        set_ocr_jurisdiction("india", "IN")


async def _run_gpt4o(storage_path: str, file_type: str) -> dict:
    """
    Primary extraction: GPT-4o via utils.ocr_engine, which reads the PDF's text
    layer or rasterises it per config.OCR_TO_USE. The path actually taken is in
    fields["_ocr_mode"], so it lands in raw_ocr_results.raw_response_jsonb.
    Returns the standard engine envelope with the full structured JSON in 'fields'.
    """
    from utils.ocr_engine import process_pdf_async, process_image_async
    ft = file_type.upper()
    if ft == "PDF":
        raw = await process_pdf_async(storage_path)
    else:
        raw = await process_image_async(storage_path)
    return {
        "engine":     OcrEngine.PADDLEOCR,   # slot = "primary OCR engine"
        "confidence": _estimate_confidence(raw),
        "raw_text":   "",
        "fields":     raw,
    }


async def _run_servam(storage_path: str) -> dict:
    """
    Fallback OCR: Sarvam AI optical-character-recognition endpoint.
    Returns raw text only (no structured fields); used for confidence boosting.
    Skipped gracefully when SARVAM_API_KEY is not configured.
    """
    _empty = {"engine": OcrEngine.SERVAM, "confidence": 0.0, "raw_text": "", "fields": {}}
    if not config.SARVAM_API_KEY:
        logger.debug("[ocr] SARVAM_API_KEY not set — skipping fallback")
        return _empty

    import requests  # already available via tally_client dependency

    def _call() -> dict:
        path = Path(storage_path)
        with open(path, "rb") as fh:
            resp = requests.post(
                "https://api.sarvam.ai/optical-character-recognition",
                headers={"api-subscription-key": config.SARVAM_API_KEY},
                files={"file": (path.name, fh, "application/octet-stream")},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    try:
        loop     = asyncio.get_event_loop()
        data     = await loop.run_in_executor(None, _call)
        raw_text = data.get("text") or data.get("result") or ""
        return {
            "engine":     OcrEngine.SERVAM,
            "confidence": 0.6 if raw_text else 0.0,
            "raw_text":   raw_text,
            "fields":     {},
        }
    except Exception as exc:
        logger.warning("[ocr] sarvam fallback error: %s", exc)
        return _empty


def _merge_ocr_results(primary: dict, fallback: dict | None) -> dict:
    """Merge two OCR results, preferring higher per-field confidence."""
    if not fallback or not fallback.get("fields"):
        return {**primary, "engine": OcrEngine.MERGED}
    merged = dict(primary.get("fields") or {})
    p_conf = primary.get("confidence_fields") or {}
    f_conf = fallback.get("confidence_fields") or {}
    for field, val in (fallback.get("fields") or {}).items():
        if field not in merged or f_conf.get(field, 0) > p_conf.get(field, 0):
            merged[field] = val
    return {
        "engine":     OcrEngine.MERGED,
        "confidence": max(primary.get("confidence", 0), fallback.get("confidence", 0)),
        "fields":     merged,
    }


# ── Structured file parser (CSV / XLSX) ───────────────────────────────────────

def _parse_structured_file(storage_path: str, file_type: str) -> dict:
    """
    Parse a CSV or XLSX invoice file into the standard OCR fields dict.
    Expected column names (case-insensitive): invoice_number, invoice_date,
    due_date, total_amount, vendor_name, vendor_gstin, and per-row line item
    columns (description, hsn_sac, quantity, unit, rate, amount).
    The first data row is treated as the invoice header; all rows as line items.
    """
    try:
        if file_type.upper() == "CSV":
            with open(storage_path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                return {}
            h = rows[0]
            return {
                "document_type":  "bill",
                "invoice_number": _s(h.get("invoice_number") or h.get("bill_number")),
                "invoice_date":   _s(h.get("invoice_date") or h.get("date")),
                "due_date":       _s(h.get("due_date")),
                "total_amount":   _num(h.get("total_amount") or h.get("total")),
                "subtotal":       _num(h.get("subtotal") or h.get("taxable_amount")),
                "currency_code":  _s(h.get("currency_code") or h.get("currency")) or "INR",
                "vendor": {
                    "name":  _s(h.get("vendor_name") or h.get("supplier_name")),
                    "gstin": _s(h.get("vendor_gstin") or h.get("gstin")),
                },
                "line_items": [
                    {
                        "description": _s(r.get("description") or r.get("item_name") or r.get("product")),
                        "hsn_sac":     _s(r.get("hsn_sac") or r.get("hsn")),
                        "quantity":    _num(r.get("quantity") or r.get("qty")),
                        "unit":        _s(r.get("unit")),
                        "rate":        _num(r.get("rate") or r.get("unit_price")),
                        "amount":      _num(r.get("amount") or r.get("line_total")),
                    }
                    for r in rows
                ] if len(rows) > 1 else [],
            }

        elif file_type.upper() == "XLSX":
            import openpyxl
            wb   = openpyxl.load_workbook(storage_path, read_only=True, data_only=True)
            ws   = wb.active
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
            if len(rows) < 2:
                return {}
            headers = [str(c).strip().lower() if c else "" for c in rows[0]]
            data    = [dict(zip(headers, row)) for row in rows[1:]]
            if not data:
                return {}
            h = data[0]
            return {
                "document_type":  "bill",
                "invoice_number": _s(h.get("invoice_number") or h.get("bill_number")),
                "invoice_date":   str(h.get("invoice_date") or h.get("date") or ""),
                "due_date":       str(h.get("due_date") or ""),
                "total_amount":   _num(h.get("total_amount") or h.get("total")),
                "currency_code":  _s(h.get("currency_code") or h.get("currency")) or "INR",
                "vendor": {
                    "name":  _s(h.get("vendor_name") or h.get("supplier_name")),
                    "gstin": _s(h.get("vendor_gstin") or h.get("gstin")),
                },
                "line_items": [
                    {
                        "description": _s(r.get("description") or r.get("item_name")),
                        "hsn_sac":     _s(r.get("hsn_sac") or r.get("hsn")),
                        "quantity":    _num(r.get("quantity") or r.get("qty")),
                        "unit":        _s(r.get("unit")),
                        "rate":        _num(r.get("rate") or r.get("unit_price")),
                        "amount":      _num(r.get("amount") or r.get("line_total")),
                    }
                    for r in data
                ] if len(data) > 1 else [],
            }
    except Exception as exc:
        logger.warning("[ocr] structured file parse failed path=%s: %s", storage_path, exc)
    return {}


# ── Data extraction ────────────────────────────────────────────────────────────

def _extract_line_items(raw_items: list) -> list[dict]:
    result = []
    for idx, li in enumerate(raw_items or [], start=1):
        amount = float(_parse_amount(li.get("amount") or 0))
        result.append({
            "line_number":      idx,
            "product_name":     _s(li.get("description") or li.get("item") or "") or "",
            "hsn_code":         _s(li.get("hsn_sac") or li.get("hsn_code")),
            "quantity":         (float(_parse_amount(li["quantity"])) if li.get("quantity") not in (None, "") else None),
            "unit":             _s(li.get("unit")),
            "unit_price":       (float(_parse_amount(li.get("rate") or li.get("unit_price"))) if (li.get("rate") or li.get("unit_price")) not in (None, "") else None),
            "discount_amount":  0.0,
            "tax_rate_percent": 0.0,
            "tax_amount":       0.0,
            "line_total":       amount,
        })
    return result


def _extract_invoice(fields: dict, upload_id: str) -> dict | None:
    """
    AR invoice: buyer is the end-customer (receivable side).
    Returns None when invoice_number or total_amount cannot be determined.
    """
    invoice_number = _s(
        fields.get("invoice_number") or fields.get("invoice_no") or fields.get("bill_number")
    )
    if not invoice_number:
        return None
    total_amount = float(_parse_amount(
        fields.get("total_amount") or fields.get("grand_total") or 0
    ))
    if total_amount == 0:
        return None

    from utils.ocr_engine import gst_amount

    taxes    = fields.get("taxes") or {}
    buyer    = fields.get("buyer") or {}
    vendor   = fields.get("vendor") or {}
    tax_total = gst_amount(taxes)
    subtotal = float(_parse_amount(
        taxes.get("taxable_amount") or fields.get("subtotal") or total_amount
    ))

    raw_cur = str(fields.get("currency_code") or "INR").upper().strip()
    currency_code = raw_cur if raw_cur in ("USD", "EUR", "GBP", "JPY", "INR") else "INR"

    return {
        "source_invoice_id": f"upload_{upload_id}",
        "invoice_number":    invoice_number,
        "invoice_date":      _parse_ocr_date(fields.get("invoice_date") or fields.get("date")) or date.today(),
        "due_date":          _parse_ocr_date(fields.get("due_date")),
        "total_amount":      total_amount,
        "subtotal":          subtotal,
        "tax_amount":        tax_total,
        "discount_amount":   float(_parse_amount(fields.get("discount") or 0)),
        "amount_paid":       0.0,
        "outstanding_amount": total_amount,
        "gstin_seller":      _gstn_clean((vendor.get("gstin") or "")),
        "gstin_buyer":       _gstn_clean((buyer.get("gstin") or "")),
        "currency_code":     currency_code,
        "original_amount":   total_amount if currency_code != "INR" else None,
        "status":            InvoiceStatus.SENT,
        "customer_name":     _s(buyer.get("name") or fields.get("buyer_name") or "Unknown Customer"),
        "customer_email":    _s(buyer.get("email") or fields.get("buyer_email")),
        "line_items":        _extract_line_items(fields.get("line_items")),
    }


def _extract_bill(fields: dict, upload_id: str) -> dict | None:
    """
    AP bill: vendor is the supplier (payable side).
    Returns None when bill_number or total_amount cannot be determined.
    """
    bill_number = _s(
        fields.get("invoice_number") or fields.get("bill_number") or fields.get("po_number")
    )
    if not bill_number:
        return None
    total_amount = float(_parse_amount(
        fields.get("total_amount") or fields.get("grand_total") or 0
    ))
    if total_amount == 0:
        return None

    from utils.ocr_engine import gst_amount

    taxes    = fields.get("taxes") or {}
    vendor   = fields.get("vendor") or {}
    tax_total = gst_amount(taxes)
    subtotal = float(_parse_amount(
        taxes.get("taxable_amount") or fields.get("subtotal") or total_amount
    ))

    raw_cur_b = str(fields.get("currency_code") or "INR").upper().strip()
    currency_code_b = raw_cur_b if raw_cur_b in ("USD", "EUR", "GBP", "JPY", "INR") else "INR"

    return {
        "source_bill_id":     f"upload_{upload_id}",
        "bill_number":        bill_number,
        "bill_date":          _parse_ocr_date(fields.get("invoice_date") or fields.get("date")) or date.today(),
        "due_date":           _parse_ocr_date(fields.get("due_date")),
        "total_amount":       total_amount,
        "subtotal":           subtotal,
        "tax_amount":         tax_total,
        "discount_amount":    float(_parse_amount(fields.get("discount") or 0)),
        "amount_paid":        0.0,
        "outstanding_amount": total_amount,
        "gstin_seller":       _gstn_clean((vendor.get("gstin") or "")),
        "currency_code":      currency_code_b,
        "original_amount":    total_amount if currency_code_b != "INR" else None,
        "supplier_name":      _s(vendor.get("name") or "Unknown Supplier"),
        "supplier_gstin":     _gstn_clean((vendor.get("gstin") or "")),
        "supplier_email":     _s(vendor.get("email")),
        "line_items":         _extract_line_items(fields.get("line_items")),
    }


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _set_upload_status(
    conn: asyncpg.Connection,
    upload_id: str,
    status: UploadStatus,
    ocr_engine: OcrEngine | None = None,
    error_msg: str | None = None,
    tenant_id: str = "",
) -> None:
    await conn.execute(
        """
        UPDATE raw_invoice_uploads
        SET status=$1,
            ocr_engine_used = COALESCE($2, ocr_engine_used),
            error_message   = COALESCE($3, error_message)
        WHERE upload_id=$4
        """,
        status, ocr_engine, error_msg, upload_id,
    )
    if tenant_id:
        await _audit(conn, tenant_id=tenant_id, table_name="raw_invoice_uploads",
                     entity_id=upload_id, action="UPDATE",
                     new_values={"status": str(status),
                                 **({"error_message": error_msg} if error_msg else {})})


async def _store_ocr_result(
    conn: asyncpg.Connection,
    upload_id: str,
    tenant_id: str,
    ocr_result: dict,
    page: int = 1,
) -> None:
    engine = ocr_result.get("engine", OcrEngine.NONE)
    await conn.execute(
        """
        INSERT INTO raw_ocr_results
            (upload_id, tenant_id, engine, page_number,
             raw_response_jsonb, confidence_jsonb)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT DO NOTHING
        """,
        upload_id, tenant_id, engine, page,
        json.dumps(ocr_result.get("fields") or {}),
        json.dumps({"overall": ocr_result.get("confidence", 0)}),
    )


async def _upsert_supplier(
    conn: asyncpg.Connection,
    tenant_id: str,
    supplier_name: str,
    supplier_gstin: str | None,
    supplier_email: str | None,
) -> str | None:
    source_id = supplier_gstin or re.sub(r"\s+", "_", supplier_name.upper())[:100]
    return await conn.fetchval(
        """
        INSERT INTO dim_suppliers
            (tenant_id, source_supplier_id, name, gstn, email, source, status, is_active)
        VALUES ($1,$2,$3,$4::char(15),$5,'UPLOAD','UNVERIFIED',true)
        ON CONFLICT (tenant_id, source, source_supplier_id) DO UPDATE SET
            name      = EXCLUDED.name,
            gstn      = COALESCE(EXCLUDED.gstn, dim_suppliers.gstn),
            email     = COALESCE(EXCLUDED.email, dim_suppliers.email),
            is_active = true
        RETURNING supplier_id
        """,
        tenant_id, source_id, supplier_name, supplier_gstin, supplier_email,
    )


async def _promote_to_stg_invoice(
    conn: asyncpg.Connection,
    tenant_id: str,
    upload_id: str,
    inv: dict,
    ocr_confidence: float,
    ocr_engine: OcrEngine,
) -> None:
    """Write AR invoice → dim_end_customers + stg_invoices + stg_invoice_line_items."""
    cust_src_id = inv.get("customer_email") or inv["customer_name"]
    end_customer_id = await conn.fetchval(
        """
        INSERT INTO dim_end_customers
            (tenant_id, source, source_customer_id, company_name, email, currency_code)
        VALUES ($1,'UPLOAD',$2,$3,$4,'INR')
        ON CONFLICT (tenant_id, source, source_customer_id) DO UPDATE
            SET company_name = EXCLUDED.company_name,
                updated_at   = now()
        RETURNING end_customer_id
        """,
        tenant_id, cust_src_id, inv["customer_name"], inv.get("customer_email"),
    )

    validation_flag = ValidationFlag.OK if ocr_confidence >= 0.7 else ValidationFlag.REVIEW

    stg_invoice_id = await conn.fetchval(
        """
        INSERT INTO stg_invoices (
            tenant_id, source, source_invoice_id, source_updated_at,
            upload_id, invoice_number, invoice_date, due_date,
            end_customer_id, currency_code,
            subtotal, discount_amount, tax_amount,
            total_amount, amount_paid, outstanding_amount,
            status, gstin_seller, gstin_buyer,
            reverse_charge, validation_flag, ocr_confidence, ocr_engine_used,
            original_amount,
            sync_status
        ) VALUES (
            $1,'UPLOAD',$2,now(),
            $3,$4,$5,$6,
            $7,$8,
            $9,$10,$11,
            $12,$13,$14,
            $15,$16,$17,
            false,$18,$19,$20,
            $21,
            'PENDING'
        )
        ON CONFLICT (tenant_id, source, source_invoice_id) DO UPDATE SET
            invoice_number     = EXCLUDED.invoice_number,
            total_amount       = EXCLUDED.total_amount,
            outstanding_amount = EXCLUDED.outstanding_amount,
            original_amount    = EXCLUDED.original_amount,
            validation_flag    = EXCLUDED.validation_flag,
            ocr_confidence     = EXCLUDED.ocr_confidence,
            sync_status        = 'PENDING',
            updated_at         = now()
        RETURNING stg_invoice_id
        """,
        tenant_id, inv["source_invoice_id"], upload_id,
        inv["invoice_number"], inv["invoice_date"], inv.get("due_date"),
        end_customer_id, inv.get("currency_code", "INR"),
        inv["subtotal"], inv["discount_amount"], inv["tax_amount"],
        inv["total_amount"], inv["amount_paid"], inv["outstanding_amount"],
        inv["status"], inv.get("gstin_seller"), inv.get("gstin_buyer"),
        validation_flag, ocr_confidence, ocr_engine,
        inv.get("original_amount"),
    )

    for li in inv.get("line_items") or []:
        await conn.execute(
            """
            INSERT INTO stg_invoice_line_items (
                stg_invoice_id, tenant_id, line_number, product_name,
                hsn_code, quantity, unit_price,
                discount_amount, tax_rate_percent, tax_amount, line_total
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT DO NOTHING
            """,
            stg_invoice_id, tenant_id,
            li["line_number"], li["product_name"],
            li.get("hsn_code"), li.get("quantity"), li.get("unit_price"),
            li["discount_amount"], li["tax_rate_percent"],
            li["tax_amount"], li["line_total"],
        )


async def _promote_to_stg_bill(
    conn: asyncpg.Connection,
    tenant_id: str,
    upload_id: str,
    bill: dict,
    ocr_confidence: float,
    ocr_engine: OcrEngine,
) -> None:
    """Write AP bill → dim_suppliers + stg_bills + stg_bill_line_items."""
    supplier_id = await _upsert_supplier(
        conn, tenant_id,
        bill["supplier_name"], bill["supplier_gstin"], bill.get("supplier_email"),
    )

    validation_flag = ValidationFlag.OK if ocr_confidence >= 0.7 else ValidationFlag.REVIEW

    stg_bill_id = await conn.fetchval(
        """
        INSERT INTO stg_bills (
            tenant_id, source, source_bill_id, source_updated_at, upload_id,
            bill_number, bill_date, due_date, supplier_id, currency_code,
            subtotal, discount_amount, tax_amount, total_amount,
            amount_paid, outstanding_amount, status, gstin_seller,
            original_amount,
            validation_flag, sync_status
        ) VALUES (
            $1,'UPLOAD',$2,now(),$3,
            $4,$5,$6,$7,$8,
            $9,$10,$11,$12,
            0,$12,'DRAFT',$13::char(15),
            $14,
            $15,'PENDING'
        )
        ON CONFLICT (tenant_id, source, source_bill_id) DO UPDATE SET
            bill_number        = EXCLUDED.bill_number,
            total_amount       = EXCLUDED.total_amount,
            outstanding_amount = EXCLUDED.outstanding_amount,
            original_amount    = EXCLUDED.original_amount,
            validation_flag    = EXCLUDED.validation_flag,
            sync_status        = 'PENDING',
            updated_at         = now()
        RETURNING stg_bill_id
        """,
        tenant_id, bill["source_bill_id"], upload_id,
        bill["bill_number"], bill["bill_date"], bill.get("due_date"),
        supplier_id, bill.get("currency_code", "INR"),
        bill["subtotal"], bill["discount_amount"], bill["tax_amount"], bill["total_amount"],
        bill["supplier_gstin"], bill.get("original_amount"),
        validation_flag,
    )

    for li in bill.get("line_items") or []:
        await conn.execute(
            """
            INSERT INTO stg_bill_line_items (
                stg_bill_id, tenant_id, line_number, product_name,
                hsn_code, quantity, unit, unit_price,
                discount_amount, tax_rate_percent, tax_amount, line_total
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT DO NOTHING
            """,
            stg_bill_id, tenant_id,
            li["line_number"], li["product_name"],
            li.get("hsn_code"), li.get("quantity"), li.get("unit"), li.get("unit_price"),
            li["discount_amount"], li["tax_rate_percent"],
            li["tax_amount"], li["line_total"],
        )


# ── Worker ─────────────────────────────────────────────────────────────────────

class UploadOcrWorker(BaseWorker):
    name           = "upload_ocr"
    notify_channel = "ch_upload_ocr"

    async def process_batch(self) -> int:
        pool  = await get_pool()
        total = 0
        total += await self._advance_received(pool)
        total += await self._advance_ocr_primary(pool)
        total += await self._advance_ocr_fallback(pool)
        total += await self._advance_validating(pool)
        return total

    # ── Stage 1: RECEIVED → PREPROCESSING ─────────────────────────────────────

    async def _advance_received(self, pool: asyncpg.Pool) -> int:
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT upload_id, tenant_id, file_name, file_type, storage_path
                    FROM raw_invoice_uploads
                    WHERE status = 'RECEIVED'
                      AND tenant_id = ANY($2::uuid[])
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT upload_id, tenant_id, file_name, file_type, storage_path
                    FROM raw_invoice_uploads
                    WHERE status = 'RECEIVED'
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

        processed = 0
        for row in rows:
            upload_id = str(row["upload_id"])
            tenant_id = str(row["tenant_id"])
            file_type = row["file_type"]
            try:
                if file_type.upper() not in {"PDF", "PNG", "JPG", "TIFF", "XLSX", "CSV"}:
                    raise ValueError(f"unsupported file_type={file_type}")
                async with pool.acquire() as conn:
                    await _set_upload_status(conn, upload_id, UploadStatus.PREPROCESSING)
                processed += 1
                logger.debug("[ocr] RECEIVED→PREPROCESSING upload=%s", upload_id)
            except Exception as exc:
                logger.exception("[ocr] pre-processing failed upload=%s", upload_id)
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _set_upload_status(
                            conn, upload_id, UploadStatus.PREPROCESS_ERROR,
                            error_msg=str(exc),
                        )
                        await self.log_error(
                            conn, tenant_id=tenant_id, stage="preprocess",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)}, upload_id=upload_id,
                        )
        return processed

    # ── Stage 2: PREPROCESSING → OCR_PRIMARY ──────────────────────────────────

    async def _advance_ocr_primary(self, pool: asyncpg.Pool) -> int:
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT upload_id, tenant_id, storage_path, file_type
                    FROM raw_invoice_uploads
                    WHERE status = 'PREPROCESSING'
                      AND tenant_id = ANY($2::uuid[])
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT upload_id, tenant_id, storage_path, file_type
                    FROM raw_invoice_uploads
                    WHERE status = 'PREPROCESSING'
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

        processed = 0
        for row in rows:
            upload_id    = str(row["upload_id"])
            tenant_id    = str(row["tenant_id"])
            storage_path = row["storage_path"]
            file_type    = row["file_type"]
            try:
                if file_type.upper() in ("CSV", "XLSX"):
                    ocr_result = {
                        "engine":     OcrEngine.NONE,
                        "confidence": 1.0,
                        "fields":     _parse_structured_file(storage_path, file_type),
                    }
                else:
                    await _set_ocr_jurisdiction(tenant_id)
                    ocr_result = await _run_gpt4o(storage_path, file_type)

                next_status = (
                    UploadStatus.VALIDATING
                    if ocr_result.get("confidence", 0) >= 0.6
                    else UploadStatus.OCR_FALLBACK
                )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _store_ocr_result(conn, upload_id, tenant_id, ocr_result)
                        await _set_upload_status(
                            conn, upload_id, next_status,
                            ocr_engine=ocr_result["engine"],
                            tenant_id=tenant_id,
                        )
                processed += 1
                logger.debug("[ocr] OCR_PRIMARY done upload=%s conf=%.2f",
                             upload_id, ocr_result.get("confidence", 0))
            except Exception as exc:
                logger.exception("[ocr] ocr_primary failed upload=%s", upload_id)
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _set_upload_status(conn, upload_id, UploadStatus.ERROR,
                                                 error_msg=str(exc), tenant_id=tenant_id)
                        await self.log_error(
                            conn, tenant_id=tenant_id, stage="ocr_primary",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)}, upload_id=upload_id,
                        )
        return processed

    # ── Stage 3: OCR_FALLBACK (Sarvam) ────────────────────────────────────────

    async def _advance_ocr_fallback(self, pool: asyncpg.Pool) -> int:
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT upload_id, tenant_id, storage_path
                    FROM raw_invoice_uploads
                    WHERE status = 'OCR_FALLBACK'
                      AND tenant_id = ANY($2::uuid[])
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT upload_id, tenant_id, storage_path
                    FROM raw_invoice_uploads
                    WHERE status = 'OCR_FALLBACK'
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

        processed = 0
        for row in rows:
            upload_id    = str(row["upload_id"])
            tenant_id    = str(row["tenant_id"])
            storage_path = row["storage_path"]
            try:
                async with pool.acquire() as conn:
                    primary_row = await conn.fetchrow(
                        "SELECT raw_response_jsonb FROM raw_ocr_results "
                        "WHERE upload_id=$1 ORDER BY created_at LIMIT 1",
                        upload_id,
                    )
                primary_fields = {}
                if primary_row:
                    primary_fields = primary_row["raw_response_jsonb"] or {}
                    if isinstance(primary_fields, str):
                        primary_fields = json.loads(primary_fields)

                fallback = await _run_servam(storage_path)
                merged   = _merge_ocr_results(
                    {"engine": OcrEngine.PADDLEOCR, "fields": primary_fields},
                    fallback,
                )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _store_ocr_result(conn, upload_id, tenant_id, merged, page=2)
                        await _set_upload_status(
                            conn, upload_id, UploadStatus.VALIDATING,
                            ocr_engine=OcrEngine.MERGED,
                            tenant_id=tenant_id,
                        )
                processed += 1
            except Exception as exc:
                logger.exception("[ocr] fallback failed upload=%s", upload_id)
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _set_upload_status(conn, upload_id, UploadStatus.ERROR,
                                                 error_msg=str(exc), tenant_id=tenant_id)
                        await self.log_error(
                            conn, tenant_id=tenant_id, stage="ocr_fallback",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)}, upload_id=upload_id,
                        )
        return processed

    # ── Stage 4: VALIDATING → DONE ────────────────────────────────────────────

    async def _advance_validating(self, pool: asyncpg.Pool) -> int:
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT u.upload_id, u.tenant_id, u.ocr_engine_used,
                           r.raw_response_jsonb, r.confidence_jsonb
                    FROM raw_invoice_uploads u
                    LEFT JOIN LATERAL (
                        SELECT raw_response_jsonb, confidence_jsonb
                        FROM raw_ocr_results
                        WHERE upload_id = u.upload_id
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) r ON true
                    WHERE u.status = 'VALIDATING'
                      AND u.tenant_id = ANY($2::uuid[])
                    ORDER BY u.created_at
                    LIMIT $1
                    FOR UPDATE OF u SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT u.upload_id, u.tenant_id, u.ocr_engine_used,
                           r.raw_response_jsonb, r.confidence_jsonb
                    FROM raw_invoice_uploads u
                    LEFT JOIN LATERAL (
                        SELECT raw_response_jsonb, confidence_jsonb
                        FROM raw_ocr_results
                        WHERE upload_id = u.upload_id
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) r ON true
                    WHERE u.status = 'VALIDATING'
                    ORDER BY u.created_at
                    LIMIT $1
                    FOR UPDATE OF u SKIP LOCKED
                    """,
                    self.batch_size,
                )

        processed = 0
        for row in rows:
            upload_id    = str(row["upload_id"])
            tenant_id    = str(row["tenant_id"])
            ocr_engine   = row["ocr_engine_used"] or OcrEngine.NONE
            raw_fields   = row["raw_response_jsonb"] or {}
            conf_json    = row["confidence_jsonb"] or {}
            if isinstance(raw_fields, str):
                raw_fields = json.loads(raw_fields)
            if isinstance(conf_json, str):
                conf_json = json.loads(conf_json)
            overall_conf = float(conf_json.get("overall", 0) or 0)

            try:
                if _is_bill(raw_fields):
                    doc = _extract_bill(raw_fields, upload_id)
                    if doc is None:
                        raise ValueError("could not extract bill: missing bill_number or total_amount")
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            await _promote_to_stg_bill(
                                conn, tenant_id, upload_id, doc,
                                ocr_confidence=overall_conf, ocr_engine=ocr_engine,
                            )
                            await _set_upload_status(conn, upload_id, UploadStatus.DONE, tenant_id=tenant_id)
                    logger.info("[ocr] DONE (bill) upload=%s bill=%s", upload_id, doc["bill_number"])
                else:
                    doc = _extract_invoice(raw_fields, upload_id)
                    if doc is None:
                        raise ValueError("could not extract invoice: missing invoice_number or total_amount")
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            await _promote_to_stg_invoice(
                                conn, tenant_id, upload_id, doc,
                                ocr_confidence=overall_conf, ocr_engine=ocr_engine,
                            )
                            await _set_upload_status(conn, upload_id, UploadStatus.DONE, tenant_id=tenant_id)
                    logger.info("[ocr] DONE (invoice) upload=%s inv=%s", upload_id, doc["invoice_number"])

                processed += 1
            except Exception as exc:
                logger.exception("[ocr] validation failed upload=%s", upload_id)
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _set_upload_status(conn, upload_id, UploadStatus.ERROR,
                                                 error_msg=str(exc), tenant_id=tenant_id)
                        await self.log_error(
                            conn, tenant_id=tenant_id, stage="validating",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)}, upload_id=upload_id,
                        )
        return processed
