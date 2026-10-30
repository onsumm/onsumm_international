"""Upload and OCR of invoice files, and serving stored documents.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Any, List, Optional
from ..integrations.ocr import process_pdf_async, enforce_pdf_page_limit, check_pdf_pages, PdfTooLongError, is_invoice_file, as_pdf
from ..integrations.invoice_gate import check_is_invoice, NotAnInvoice
from ..domain.ingest import ingest_invoice
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
import logging as _logging

from fastapi import APIRouter

router = APIRouter()

from ..core.storage import UPLOADS_DIR
from ..core import object_store
from ..core.vendor_identity import _effective_pan
from ..core.jurisdiction import get_jurisdiction
from ..core.money import currency_symbol
from utils.ocr_engine import set_ocr_jurisdiction

def _set_ocr_jurisdiction_for(tenant_id: str) -> None:
    """Tell the OCR prompt which country's invoices to expect (utils/ocr_engine.py):
    an Indian tenant keeps the GST prompt unchanged; others read VAT / sales tax."""
    j = get_jurisdiction(tenant_id)
    set_ocr_jurisdiction(j.profile, j.country_code)


@router.post("/api/invoices/ocr")
async def ocr_invoice(request: Request):
    # Stateless OCR — no row is read or written, so there is no tenant predicate
    # to add. The call is still asserted to be authenticated so this route cannot
    # become an unauthenticated way to burn OCR spend, and so the route-map guard
    # can tell "scoped" apart from "forgotten".
    _set_ocr_jurisdiction_for(require_tenant())
    async with request.form() as form:
        file: UploadFile = form.get("file")
        if file is None:
            raise HTTPException(status_code=422, detail="file field is required")
        if not is_invoice_file(file.filename):
            raise HTTPException(status_code=400, detail="Upload a PDF or an image (JPG, PNG, WebP)")
        file_bytes, safe_name = as_pdf(await file.read(), Path(file.filename).name)
    enforce_pdf_page_limit(file_bytes, safe_name)

    # Write to temp path for OCR processing
    stored_path = UPLOADS_DIR / safe_name
    with open(stored_path, "wb") as f:
        f.write(file_bytes)

    try:
        await check_is_invoice(str(stored_path))
    except NotAnInvoice as e:
        # An answer, not an error: the upload was looked at and isn't a bill.
        stored_path.unlink(missing_ok=True)
        return {"_source_file": safe_name, "_not_invoice": str(e)}

    try:
        result = await process_pdf_async(str(stored_path))
        result["_source_file"] = safe_name
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class IngestRequest(BaseModel):
    payload: dict[str, Any]
    company_id: str | None = None
    brand_id: str | None = None
    cost_center_id: str | None = None

@router.post("/api/invoices/ocr-bulk")
async def ocr_invoices_bulk(files: List[UploadFile] = File(...)):
    # See ocr_invoice: stateless OCR, authenticated but with no row to scope.
    _set_ocr_jurisdiction_for(require_tenant())

    async def process_one(upload: UploadFile) -> dict:
        if not is_invoice_file(upload.filename):
            return {"_source_file": upload.filename, "_error": "Upload a PDF or an image (JPG, PNG, WebP)"}
        try:
            file_bytes, safe_name = as_pdf(await upload.read(), Path(upload.filename).name)
        except HTTPException as e:
            return {"_source_file": upload.filename, "_error": e.detail}
        try:
            check_pdf_pages(file_bytes, safe_name)
        except PdfTooLongError as e:
            return {"_source_file": safe_name, "_error": str(e)}
        stored_path = UPLOADS_DIR / safe_name
        with open(stored_path, "wb") as f:
            f.write(file_bytes)
        try:
            await check_is_invoice(str(stored_path))
        except NotAnInvoice as e:
            stored_path.unlink(missing_ok=True)
            return {"_source_file": safe_name, "_not_invoice": str(e)}
        try:
            result = await process_pdf_async(str(stored_path))
            result["_source_file"] = safe_name
            return result
        except Exception as e:
            return {"_source_file": safe_name, "_error": str(e)}

    results = await asyncio.gather(*[process_one(f) for f in files])
    return {"results": results}

@router.get("/api/docs/{upload_id}")
def serve_doc(upload_id: str, signed: bool = Query(default=False)):
    # The upload_id being a UUID makes it hard to guess; it does not make the
    # document the caller's. Without this predicate any logged-in user could
    # fetch any tenant's invoice PDF by id — so the tenant is matched here, and a
    # document belonging to someone else 404s exactly like one that is absent.
    tenant = require_tenant()
    file_name = None
    storage_path = None
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_name, storage_path FROM raw_invoice_uploads WHERE upload_id = %s AND tenant_id = %s",
                (upload_id, tenant),
            )
            row = cur.fetchone()
            if row:
                file_name, storage_path = row
    except Exception as e:
        put_conn(conn, close=True)
        conn = None
        raise HTTPException(status_code=500, detail=str(e))
    else:
        put_conn(conn)
        conn = None

    # Fallback: scan stored_uploads for a file whose path contains the upload_id
    if not file_name:
        matches = list(UPLOADS_DIR.glob("*.pdf"))
        # try to find by upload_id embedded in fact_invoices
        _fb_conn = None
        try:
            _fb_conn = get_conn()
            with _fb_conn.cursor() as cur:
                cur.execute(
                    "SELECT u.file_name, u.storage_path FROM fact_invoices fi JOIN raw_invoice_uploads u ON fi.upload_id=u.upload_id WHERE fi.upload_id=%s AND fi.tenant_id=%s LIMIT 1",
                    (upload_id, tenant),
                )
                row = cur.fetchone()
                if row:
                    file_name, storage_path = row
            put_conn(_fb_conn)
        except Exception:
            if _fb_conn: put_conn(_fb_conn, close=True)

    if not file_name:
        raise HTTPException(status_code=404, detail="Document not found")

    # The tenant has been matched above; only now may a link be handed out.
    if signed:
        link = object_store.signed_link_response(storage_path)
        if link:
            return link

    # S3 (or the legacy file_data column for rows not yet migrated).
    _db_conn = None
    try:
        from fastapi.responses import Response
        _db_conn = get_conn()
        with _db_conn.cursor() as cur:
            # Keyed on upload_id, not file_name: uploads are stored under the
            # client's own filename in one flat namespace, so two tenants who both
            # upload "invoice.pdf" collide — and a file_name lookup would then hand
            # this tenant the other one's document.
            pdf = object_store.invoice_pdf(cur, upload_id, tenant)
        put_conn(_db_conn)
        _db_conn = None
        if pdf:
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{file_name}"',
                    "Cache-Control": "max-age=3600",
                },
            )
    except Exception:
        _logging.getLogger(__name__).exception("could not load stored PDF for upload %s", upload_id)
        if _db_conn:
            put_conn(_db_conn, close=True)

    # Fallback to local disk (dev environment)
    file_path = UPLOADS_DIR / file_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_name,
        headers={"Cache-Control": "max-age=3600"},
    )

def _tenant_buyer_block(cur, tenant_id: str, brand_id: str | None = None) -> dict:
    """Who the bill is billed TO — always us, read from our own records.

    On an AP bill the buyer is this tenant by definition, so taking it from the
    document is both unnecessary and unsafe: a misread "Bill To" block used to put
    another company's name and GSTIN on our own payable, and the buyer GSTIN's
    state code drives the inter-state/IGST check on the review screen. Nothing
    downstream ever stored the extracted buyer anyway — GET /api/bills/{id} already
    synthesises its buyer block from dim_tenants — so this makes the review screen
    agree with what the bill will actually show once ingested.

    The NAME is the brand currently selected in the UI, not the tenant's legal
    name: a tenant bills under whichever brand the user is working in, and that is
    the name that belongs on the bill. `brand_id` is resolved against `brands`
    scoped to this tenant, so a caller cannot name another tenant's brand; an
    unknown or absent brand falls back to the tenant's legal name.

    GSTIN is the tenant's — `brands` carries no GSTIN — and PAN is derived from it.

    Returns {} when the tenant row is missing, which the caller treats as "could
    not source the buyer" rather than substituting anything.

    Address is deliberately absent: dim_tenants has no address column, so that one
    field stays OCR-sourced and editable.
    """
    cur.execute(
        """SELECT COALESCE(NULLIF(TRIM(legal_name), ''), company_name), gstn
           FROM dim_tenants WHERE tenant_id = %s""",
        (tenant_id,),
    )
    row = cur.fetchone()
    if not row:
        return {}
    tenant_name, gstn = row[0] or "", (row[1] or "").strip()

    brand_name = ""
    if brand_id:
        cur.execute(
            "SELECT name FROM brands WHERE id = %s::uuid AND tenant_id = %s::uuid",
            (brand_id, tenant_id),
        )
        brand_row = cur.fetchone()
        brand_name = (brand_row[0] or "").strip() if brand_row else ""

    return {
        "name":  brand_name or tenant_name,
        "brand": brand_name,
        "gstin": gstn,
        "pan":   _effective_pan(gstn, None) or "",
    }

def _verified_vendor_by_pan(cur, tenant_id: str, gstin: str | None,
                            pan: str | None) -> dict | None:
    """The vendor master row for the legal entity that issued this bill, but only
    when we trust it enough to copy its details verbatim onto the bill.

    Identity is PAN, not GSTIN. A company holds one PAN but a separate GSTIN per
    state, so a branch invoice legitimately carries a GSTIN we have never seen for a
    vendor we already know — matching on GSTIN alone (what this endpoint used to do)
    missed those and left the AP team retyping bank details we already hold.

    Vendor verification and BANK verification are separate gates, because they
    protect against different things. Being KYB/GST verified
    (dim_suppliers.status = 'VERIFIED') is enough to copy identity and contact
    details — a name or email is not a payment instruction. Serving a bank account
    additionally requires that account to be verified, since invoice bank blocks are
    the classic payment-redirection vector.

    Conflating the two meant a KYB-verified vendor with no verified bank account
    returned None here, so the AP team retyped an email and PAN we already held.
    `bank_details` is therefore None until the bank gate passes, and `bank_verified`
    says which of the two we have — the caller must not fall back to an unverified
    account.

    Bank resolution mirrors disbursement_api._get_verified_vendor_account: prefer
    supplier_bank_accounts (verified, primary first), fall back to the legacy
    dim_suppliers.bank_* columns only when bank_verified is set.
    """
    eff_pan = _effective_pan(gstin, pan)
    if not eff_pan:
        return None
    cur.execute(
        """
        SELECT s.supplier_id, s.name, s.gstn, s.email, s.payment_terms_days,
               s.bank_account, s.bank_ifsc, s.bank_name, s.bank_verified,
               sba.account_number, sba.ifsc, sba.bank_name, sba.name_at_bank
        FROM dim_suppliers s
        LEFT JOIN LATERAL (
            SELECT account_number, ifsc, bank_name, name_at_bank
            FROM supplier_bank_accounts
            WHERE tenant_id = s.tenant_id AND supplier_id = s.supplier_id
              AND verified = true
            ORDER BY is_primary DESC, created_at ASC
            LIMIT 1
        ) sba ON true
        WHERE s.tenant_id = %s
          AND s.is_active = true
          AND s.status    = 'VERIFIED'
          AND upper(COALESCE(
                CASE WHEN length(trim(s.gstn)) = 15
                     THEN substring(trim(s.gstn) FROM 3 FOR 10) END,
                NULLIF(trim(s.pan), '')
              )) = %s
        ORDER BY (sba.account_number IS NOT NULL) DESC,
                 (s.bank_verified AND s.bank_account IS NOT NULL) DESC,
                 s.created_at ASC
        LIMIT 1
        """,
        (tenant_id, eff_pan),
    )
    row = cur.fetchone()
    if not row:
        return None
    (sid, name, master_gstn, email, terms,
     legacy_acct, legacy_ifsc, legacy_bank, legacy_bank_verified,
     acct, ifsc, bank_name, name_at_bank) = row

    # Only a VERIFIED account may be served. Without one the caller keeps whatever
    # the document printed, which a human still has to confirm — silently offering
    # an unverified stored account as though we vouched for it is the failure mode
    # this gate exists to prevent.
    if acct:
        bank = {"account_no": acct, "ifsc": ifsc or "",
                "bank_name": bank_name or "", "account_name": name_at_bank or name}
    elif legacy_bank_verified and legacy_acct:
        bank = {"account_no": legacy_acct, "ifsc": legacy_ifsc or "",
                "bank_name": legacy_bank or "", "account_name": name}
    else:
        bank = None

    ocr_gstin   = (gstin or "").strip().upper()
    master_gstn = (master_gstn or "").strip().upper()
    return {
        "supplier_id":  str(sid),
        # PAN means the same company billed from a GSTIN we have not seen before —
        # the caller keeps the document's GSTIN so the state code stays truthful.
        "matched_on":   "GSTIN" if (ocr_gstin and ocr_gstin == master_gstn) else "PAN",
        "pan":          eff_pan,
        "name":         name,
        "master_gstin": master_gstn,
        "email":        email or "",
        "payment_terms_days": terms,
        "bank_verified": bank is not None,
        "bank_details": bank,
    }

def _last_bill_category(cur, tenant_id: str, supplier_id: str | None,
                       vendor_gstin: str, vendor_name: str) -> str | None:
    """The category this vendor's most recent bill was filed under, or None.

    A vendor bills for the same thing every month, so the previous bill's category is
    a better default than re-deriving it from line items each time.

    When the vendor has been resolved to a verified master record, match on its
    supplier_id. The GSTIN/name fallbacks below miss the very case
    _verified_vendor_by_pan exists for: a branch invoice carries a GSTIN we have never
    seen, so the GSTIN test fails, and the name test then has to survive an OCR read
    character-for-character (ledger names arrive decorated, e.g.
    "C. B. COMPANY- (1-Apr-23)"). An unverified vendor keeps the old behaviour, since
    there is no trusted identity to key on.
    """
    if supplier_id:
        cur.execute(
            """
            SELECT fi.category
            FROM fact_invoices fi
            WHERE fi.tenant_id    = %s
              AND fi.supplier_id  = %s
              AND fi.invoice_type = 'AP'
              AND COALESCE(TRIM(fi.category), '') != ''
            ORDER BY fi.created_at DESC
            LIMIT 1
            """,
            (tenant_id, supplier_id),
        )
    elif vendor_name or vendor_gstin:
        cur.execute(
            """
            SELECT fi.category
            FROM fact_invoices fi
            JOIN dim_suppliers s ON s.supplier_id = fi.supplier_id
            WHERE fi.tenant_id = %s
              AND fi.category IS NOT NULL
              AND fi.category != ''
              AND (
                (%s != '' AND s.gstn = %s)
                OR s.name ILIKE %s
              )
            ORDER BY fi.created_at DESC
            LIMIT 1
            """,
            (tenant_id, vendor_gstin, vendor_gstin, vendor_name),
        )
    else:
        return None
    row = cur.fetchone()
    return row[0] if row else None

class EnrichRequest(BaseModel):
    invoice: dict
    company_id: str | None = None
    # The brand the user is working in — its name is what goes on the bill as the
    # buyer. Validated against this tenant's brands server-side.
    brand_id: str | None = None

@router.post("/api/enrich-invoice")
async def enrich_invoice(req: EnrichRequest):
    """Use GPT-4o-mini to auto-select ledger, category, bank details, and generate notes."""
    import json as _json
    from openai import AsyncOpenAI

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    inv = req.invoice

    # 1. Fetch ledger accounts for this tenant
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT account_id, account_name, account_code, account_type FROM dim_ledger_accounts WHERE tenant_id=%s AND is_active=true ORDER BY account_name",
                (tenant,)
            )
            ledger_rows = cur.fetchall()

            # 2. The buyer on an AP bill is always THIS tenant, taken from our own
            #    records rather than the document. Replaces the previous behaviour
            #    where a misread "Bill To" block put someone else's name/GSTIN on
            #    our payable.
            #
            # 3. Resolve the vendor against our master by PAN (not GSTIN) and copy
            #    its details when it is fully verified — see _verified_vendor_by_pan.
            #
            # Both are best-effort. This endpoint's main job is the ledger/category
            # recommendation, and neither lookup is the last line of defence: the
            # buyer block is display-only (GET /api/bills/{id} re-derives it from
            # dim_tenants regardless), and a missed vendor match just leaves the
            # document's own values in place for a human to check. Failing the whole
            # request over either would cost the AP team the ledger suggestions too.
            buyer_block, vendor_master = {}, None
            try:
                buyer_block = _tenant_buyer_block(cur, tenant, req.brand_id)
                vendor_in = inv.get("vendor") or {}
                vendor_master = _verified_vendor_by_pan(
                    cur, tenant, vendor_in.get("gstin"), vendor_in.get("pan"),
                )
            except Exception:
                _logging.exception("[enrich] buyer/vendor-master lookup failed for tenant %s", tenant)
                conn.rollback()
            vendor_bank = vendor_master["bank_details"] if vendor_master else None
    finally:
        put_conn(conn)

    ledger_list = [{"id": str(r[0]), "name": r[1], "code": r[2], "type": r[3]} for r in ledger_rows]

    # Tier 1: reuse category from past bills of the same vendor (skip GPT if found)
    vendor_name = (inv.get("vendor") or {}).get("name", "")
    vendor_gstin = ((inv.get("vendor") or {}).get("gstin") or "").strip()
    past_category = None
    if vendor_master or vendor_name or vendor_gstin:
        conn2 = get_conn()
        try:
            with conn2.cursor() as cur:
                past_category = _last_bill_category(
                    cur, tenant,
                    vendor_master["supplier_id"] if vendor_master else None,
                    vendor_gstin, vendor_name,
                )
        finally:
            put_conn(conn2)

    line_items = inv.get("line_items") or []
    vendor_name = (inv.get("vendor") or {}).get("name", "")
    vendor_email = (inv.get("vendor") or {}).get("email", "")
    inv_date = inv.get("invoice_date", "")
    total = inv.get("total_amount", "")
    existing_bank = inv.get("bank_details") or {}
    bank_missing = not (existing_bank.get("account_no") or existing_bank.get("bank_name"))

    CATEGORIES = ["Rent", "Reimbursement", "Travel & Expenses", "Utilities", "Software / SaaS", "Food & Beverages"]

    # If vendor category already known — skip GPT for category, only ask for ledgers + notes
    if past_category:
        category_instruction = f'- "category": must be exactly "{past_category}" — taken from this vendor\'s billing history, do not change.'
    else:
        category_instruction = f"""- "category": pick EXACTLY one from this list (copy the string verbatim):
  {_json.dumps(CATEGORIES)}

  Decision rules — apply in order, stop at first match:
  1. Any line item describes cloud/hosting/SaaS/subscription/API/software/domain/licence → "Software / SaaS"
  2. Any line item is rent/lease/office space/coworking → "Rent"
  3. Any line item is electricity/internet/broadband/water/telephone/mobile → "Utilities"
  4. Any line item is flight/hotel/cab/Ola/Uber/train/travel/accommodation → "Travel & Expenses"
  5. Any line item is food/beverage/catering/restaurant/meal/snack/refreshment → "Food & Beverages"
  6. Any line item is employee reimbursement/petty cash/expense claim → "Reimbursement"
  7. If none match clearly → pick the closest one; never return null or a value outside the list."""

    line_items_summary = _json.dumps(
        [{"description": li.get("description", ""), "hsn_sac": li.get("hsn_sac", ""), "amount": li.get("amount", "")}
         for li in line_items],
        indent=2
    )

    # The tenant's country and books currency (core/jurisdiction.py): an Indian
    # tenant gets the original wording; others are not described as Indian.
    _jur = get_jurisdiction(tenant)
    if _jur.profile == "india":
        system_msg = (
            "You are a senior Indian accounts payable analyst. "
            "You classify vendor invoices for an Indian SME. "
            "You always follow the decision rules exactly and never invent new category names."
        )
    else:
        system_msg = (
            "You are a senior accounts payable analyst. "
            f"You classify vendor invoices for an SME based in {_jur.country_code} that keeps its books in {_jur.base_currency}. "
            "You always follow the decision rules exactly and never invent new category names."
        )

    user_msg = f"""Classify and enrich this invoice. Return a JSON object with these keys:

{category_instruction}
- "notes": 1-2 sentence professional summary for the AP team (mention vendor, purpose, amount).
- "line_items": array the same length as INPUT LINE ITEMS. Each element: {{"account_id": "<id>", "account_name": "<name>"}} — choose the best matching ledger account for each line item based on its description and HSN/SAC code. Use null only if truly no ledger fits.

INVOICE
Vendor: {vendor_name}{f" ({vendor_email})" if vendor_email else ""}
Date: {inv_date} | Total: {currency_symbol(_jur.base_currency)}{total}

INPUT LINE ITEMS:
{line_items_summary}

AVAILABLE LEDGER ACCOUNTS:
{_json.dumps(ledger_list, indent=2)}

Return ONLY a valid JSON object — no markdown, no explanation."""

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = _json.loads(resp.choices[0].message.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI enrichment failed: {e}")

    # Validate category is within allowed list; reset to closest if GPT hallucinated
    if not past_category:
        picked = result.get("category", "")
        if picked not in CATEGORIES:
            # case-insensitive rescue
            picked_lower = picked.lower()
            match = next((c for c in CATEGORIES if c.lower() in picked_lower or picked_lower in c.lower()), None)
            result["category"] = match if match else "Reimbursement"

    # Always enforce past_category — GPT cannot override it
    if past_category:
        result["category"] = past_category

    # Buyer identity is ours, never the document's — always returned, never conditional.
    if buyer_block:
        result["buyer"] = buyer_block

    # A verified vendor on file outranks the document. Bank details printed on an
    # invoice are the classic payment-redirection vector, so once we have a KYB- and
    # bank-verified master record for this PAN we serve OUR account, overwriting the
    # invoice's block rather than only filling a gap. Without a match we fall back to
    # the old behaviour: fill only when the document said nothing.
    if vendor_master:
        result["vendor_master"] = {
            k: vendor_master[k]
            for k in ("supplier_id", "matched_on", "pan", "name", "master_gstin",
                      "email", "payment_terms_days", "bank_verified")
        }
        # The GSTIN stays as printed: on a PAN match this bill came from a different
        # state branch, and overwriting it would put the wrong state code on the bill
        # and flip the inter-state IGST determination.
        result["vendor"] = {
            "name":  vendor_master["name"],
            "gstin": (inv.get("vendor") or {}).get("gstin") or vendor_master["master_gstin"],
            "pan":   vendor_master["pan"],
            "email": vendor_master["email"] or (inv.get("vendor") or {}).get("email") or "",
        }
        if vendor_bank and vendor_bank.get("account_no"):
            result["bank_details"] = vendor_bank
    elif bank_missing and vendor_bank:
        result["bank_details"] = vendor_bank

    return result

@router.post("/api/invoices/ingest")
def ingest(req: IngestRequest):
    try:
        result = ingest_invoice(req.payload, tenant_id=require_tenant())
        # Stamp brand_id and (optional) cost_center_id on the newly created invoice.
        if (req.brand_id or req.cost_center_id) and result.get("invoice_id"):
            conn = get_conn()
            try:
                with conn.cursor() as _cur:
                    if req.brand_id:
                        _cur.execute(
                            "UPDATE fact_invoices SET brand_id=%s::uuid WHERE invoice_id=%s::uuid",
                            (req.brand_id, result["invoice_id"])
                        )
                        # Also stamp the supplier with this brand (first time only)
                        if result.get("supplier_id"):
                            _cur.execute(
                                "UPDATE dim_suppliers SET brand_id=%s::uuid WHERE supplier_id=%s::uuid AND brand_id IS NULL",
                                (req.brand_id, result["supplier_id"])
                            )
                    if req.cost_center_id:
                        _cur.execute(
                            "UPDATE fact_invoices SET cost_center_id=%s::uuid WHERE invoice_id=%s::uuid",
                            (req.cost_center_id, result["invoice_id"])
                        )
                conn.commit()
            except Exception:
                conn.rollback()
            finally:
                put_conn(conn)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
