from ..core.storage import UPLOADS_DIR as _UPLOADS_DIR
from ..core import object_store
import logging
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

import psycopg2
from psycopg2.extras import Json

from ..config import DB_URL, TENANT_ID as DEFAULT_TENANT_ID, TENANT_NAME, TENANT_GSTN, get_conn, put_conn

_log = logging.getLogger(__name__)
_FX_API_URL = "https://open.er-api.com/v6/latest/INR"
_VALID_FX = {"USD", "EUR", "GBP", "JPY"}


def _s(val):
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _parse_date(val):
    if not val or not str(val).strip():
        return None
    s = str(val).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d",
                "%d-%b-%Y", "%d/%b/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _num(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _infer_due_date(bill_date: date | None, payment_terms_raw: str | None) -> date | None:
    """Calculate due date from bill_date + payment terms string (e.g. 'Net 30 days' → +30).
    Defaults to 30 days when payment terms are absent."""
    from datetime import timedelta
    if not bill_date:
        return None
    if not payment_terms_raw:
        return bill_date + timedelta(days=30)
    s = str(payment_terms_raw).lower().strip()
    if any(w in s for w in ("immediate", "due on receipt", "upon receipt", "cash")):
        return bill_date
    m = re.search(r"(\d+)", s)
    if m:
        return bill_date + timedelta(days=int(m.group(1)))
    # Unrecognised terms string — fall back to 30 days
    return bill_date + timedelta(days=30)


def _gstn_clean(raw):
    if not raw:
        return None
    s = re.sub(r"\s+", "", str(raw)).upper()
    s = re.sub(r"^O", "0", s)
    return s if len(s) == 15 else None


from ..core.jurisdiction import jurisdiction_for_cursor  # noqa: E402


def _fiscal(d, start_month: int = 4):
    """(fiscal year, fiscal quarter, calendar month) for a date.

    `start_month` is the month the tenant's fiscal year begins
    (core/jurisdiction.py): April for India, which is what this always used,
    January for most other countries. The year is the one the fiscal year
    starts in; the month stays the calendar month, as before.
    """
    if not d:
        return None, None, None
    fy = d.year if d.month >= start_month else d.year - 1
    fq = (d.month - start_month) % 12 // 3 + 1
    return fy, fq, d.month


def _indian_fiscal(d):
    return _fiscal(d, 4)


def _fetch_live_rates_and_store(cur) -> dict[str, tuple[str, Decimal]]:
    """Fetch live rates from open.er-api.com, store in dim_fx_rates, return {code: (rate_id, inr_per_unit)}."""
    import httpx

    try:
        resp = httpx.get(_FX_API_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _log.warning("[fx] live rate fetch failed: %s", exc)
        return {}

    if data.get("result") != "success":
        _log.warning("[fx] API returned result=%s", data.get("result"))
        return {}

    _Q6 = Decimal("0.000001")
    today = date.today()
    result: dict[str, tuple[str, Decimal]] = {}

    for code in _VALID_FX:
        raw = data.get("rates", {}).get(code)
        if not raw:
            continue
        inr_per_unit = (Decimal(1) / Decimal(str(raw))).quantize(_Q6, rounding=ROUND_HALF_UP)
        try:
            cur.execute(
                """INSERT INTO dim_fx_rates (rate_date, currency_code, inr_per_unit, source, is_business_day)
                   VALUES (%s, %s, %s, 'PRIMARY', %s)
                   ON CONFLICT (rate_date, currency_code) DO UPDATE SET
                       inr_per_unit = EXCLUDED.inr_per_unit, source = EXCLUDED.source, fetched_at = now()
                   RETURNING rate_id""",
                (today, code, inr_per_unit, today.weekday() < 5),
            )
            row = cur.fetchone()
            if row:
                result[code] = (str(row[0]), inr_per_unit)
        except Exception as exc:
            _log.warning("[fx] failed to store rate for %s: %s", code, exc)

    if result:
        _log.info("[fx] fetched live rates: %s", {k: str(v[1]) for k, v in result.items()})
    return result


def _tenant_base_currency(cur, tid: str | None) -> str:
    """The currency the tenant keeps its books in (dim_tenants.base_currency).

    INR when unset or before crypto_migrations.py has added the column, which is
    what every tenant was before the crypto rail's foreign tenants existed.
    """
    if not tid:
        return "INR"
    try:
        cur.execute("SAVEPOINT base_ccy")
        cur.execute("SELECT base_currency FROM dim_tenants WHERE tenant_id = %s", (tid,))
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT base_ccy")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT base_ccy")
        return "INR"
    value = (row[0] if row else None) or "INR"
    return str(value).strip().upper()


_INDIA_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "INR")
_INTL_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "INR", "SGD", "AED", "AUD", "CAD", "CHF", "HKD")


def _bill_currency(cur, payload: dict, tid: str | None) -> str:
    """The bill's currency from OCR, defaulting to the tenant's books currency.

    An INR-books tenant keeps the original rule exactly: one of USD / EUR / GBP /
    JPY / INR, anything else (or nothing) read as INR. A tenant that keeps
    another currency (core/jurisdiction.py) defaults to that currency instead,
    and also accepts it and a few other common ISO codes.
    """
    base = _tenant_base_currency(cur, tid)
    if base == "INR":
        raw = str(payload.get("currency_code") or "INR").upper().strip()
        return raw if raw in _INDIA_CURRENCIES else "INR"
    raw = str(payload.get("currency_code") or base).upper().strip()
    return raw if raw in _INTL_CURRENCIES or raw == base else base


def _inr_per_unit(cur, currency_code: str):
    """(fx_rate_id, INR per one unit of currency_code), or (None, None)."""
    if currency_code == "INR":
        return None, Decimal("1")
    try:
        cur.execute(
            "SELECT rate_id, inr_per_unit FROM dim_fx_rates WHERE currency_code = %s ORDER BY rate_date DESC LIMIT 1",
            (currency_code,),
        )
        rate_row = cur.fetchone()
        if rate_row:
            return str(rate_row[0]), Decimal(str(rate_row[1]))
    except Exception:
        pass
    live = _fetch_live_rates_and_store(cur)
    if currency_code in live:
        rate_id, rate = live[currency_code]
        return rate_id, Decimal(str(rate))
    return None, None


def _fx_convert(cur, currency_code: str, total_amount, subtotal=None, tax_amount=None, outstanding_amount=None,
                base_currency: str = "INR"):
    """Convert a bill's amounts into the tenant's books currency.

    `base_currency` is INR for Indian tenants (the original behaviour). For a
    tenant that keeps its books in another currency (the crypto rail's Lumen
    Labs keeps USD), a bill already in that currency is not converted at all,
    and any other currency converts through the INR rates we hold:
    rate = (INR per bill unit) / (INR per base unit).
    Returns (total_amount, subtotal, tax_amount, outstanding_amount, fx_rate_id).
    """
    base_currency = (base_currency or "INR").upper()
    if currency_code == base_currency or not total_amount:
        return total_amount, subtotal, tax_amount, outstanding_amount, None
    if base_currency != "INR":
        rate_id, bill_inr = _inr_per_unit(cur, currency_code)
        _, base_inr = _inr_per_unit(cur, base_currency)
        if bill_inr is None or not base_inr:
            _log.error("[fx] no %s/%s rate available — bill will remain unconverted", currency_code, base_currency)
            return total_amount, subtotal, tax_amount, outstanding_amount, None
        cross = bill_inr / base_inr
        _Qb = Decimal("0.01")

        def _conv_base(val):
            if val is None or val == 0:
                return val
            return float((Decimal(str(val)) * cross).quantize(_Qb))

        return (_conv_base(total_amount), _conv_base(subtotal), _conv_base(tax_amount),
                _conv_base(outstanding_amount or total_amount), rate_id)

    inr_per_unit = None
    fx_rate_id = None

    # 1. Try DB (cached rate)
    try:
        cur.execute(
            "SELECT rate_id, inr_per_unit FROM dim_fx_rates WHERE currency_code = %s ORDER BY rate_date DESC LIMIT 1",
            (currency_code,),
        )
        rate_row = cur.fetchone()
        if rate_row:
            fx_rate_id = str(rate_row[0])
            inr_per_unit = Decimal(str(rate_row[1]))
    except Exception:
        pass

    # 2. No cached rate — fetch live from exchange rate API
    if inr_per_unit is None:
        live = _fetch_live_rates_and_store(cur)
        if currency_code in live:
            fx_rate_id, inr_per_unit = live[currency_code]

    if inr_per_unit is None:
        _log.error("[fx] no rate available for %s — bill will remain unconverted", currency_code)
        return total_amount, subtotal, tax_amount, outstanding_amount, None

    _Q = Decimal("0.01")

    def _conv(val):
        if val is None or val == 0:
            return val
        return float((Decimal(str(val)) * inr_per_unit).quantize(_Q))

    return _conv(total_amount), _conv(subtotal), _conv(tax_amount), _conv(outstanding_amount or total_amount), fx_rate_id


def ingest_invoice(payload: dict, tenant_id: str | None = None) -> dict:
    tid = tenant_id or DEFAULT_TENANT_ID
    conn = get_conn()
    conn.autocommit = False
    result = {}
    try:
        with conn.cursor() as cur:
            _ensure_tenant(cur, tid)
            conn.commit()

            vendor = payload.get("vendor") or {}
            taxes = payload.get("taxes") or {}
            line_items = payload.get("line_items") or []
            source_file = payload.get("_source_file", "upload")
            file_size = len(str(payload).encode())
            uploaded_by_name = payload.get("_uploaded_by_name")
            uploaded_by_role = payload.get("_uploaded_by_role")
            department = payload.get("_department")
            category = payload.get("_category")

            bank_details = payload.get("bank_details") or {}
            supplier_id = _upsert_supplier(cur, vendor, tid, bank_details)

            # The OCR step left the PDF in the local upload staging dir. It is
            # kept in file_data as the backup and, with S3 on, copied to S3.
            _pdf_path = _UPLOADS_DIR / source_file
            _file_bytes = _pdf_path.read_bytes() if _pdf_path.exists() else None
            if _file_bytes is None and object_store.enabled():
                # A bill the inbox agent drafted, opened on a box whose disk
                # no longer has the staged copy.
                _file_bytes = object_store.load(
                    f"s3://{object_store.S3_BUCKET}/{object_store.key('agent-drafts', tid, source_file)}")

            upload_id = _upsert_upload(cur, source_file, file_size, uploaded_by_name, uploaded_by_role, tid, _file_bytes)
            if _file_bytes and object_store.enabled():
                _store_invoice_pdf(cur, upload_id, tid, source_file, _file_bytes)
            ocr_id = _insert_ocr_result(cur, upload_id, payload, tid)

            bill_num = (_s(payload.get("invoice_number")) or source_file)
            bill_row = _upsert_bill(cur, payload, supplier_id, upload_id, source_file, category, tid)
            stg_bill_id = str(bill_row[0]) if bill_row else None

            # Extract currency from OCR result; default to the tenant's books currency
            currency_code = _bill_currency(cur, payload, tid)
            original_amount = _num(payload.get("total_amount"))

            invoice_id = None
            is_duplicate = False
            if bill_row:
                invoice_id, is_duplicate = _upsert_fact_invoice(
                    cur, bill_row, bill_num, source_file, category, tid, department,
                    currency_code=currency_code, original_amount=original_amount
                )
            if not invoice_id:
                raise ValueError(f"Failed to create or locate invoice record for bill '{bill_num}'")

            if stg_bill_id and line_items:
                _insert_line_items(cur, stg_bill_id, line_items, taxes, tid)
                _log_ledger_mappings(cur, line_items, supplier_id, tid)

            conn.commit()
            result = {
                "status": "success",
                "supplier_id": supplier_id,
                "upload_id": upload_id,
                "ocr_id": ocr_id,
                "stg_bill_id": stg_bill_id,
                "invoice_id": invoice_id,
                "line_items_count": len(line_items),
                "is_duplicate": is_duplicate,
                "duplicate_invoice_number": bill_num if is_duplicate else None,
            }
    except Exception as exc:
        conn.rollback()
        put_conn(conn, close=True)
        raise exc
    else:
        put_conn(conn)
        # Fire-and-forget: enrich the tenant fact graph off the request path.
        # Lazy import avoids an import cycle; enqueue never raises.
        try:
            if result.get("invoice_id"):
                from ..domain import fact_graph
                fact_graph.enqueue(fact_graph.enrich_bill, tid, result["invoice_id"])
        except Exception:
            pass
    return result


def _ensure_tenant(cur, tid: str):
    # Use NULL gstn if another tenant already owns this GSTN, so the row is still created
    cur.execute(
        """INSERT INTO dim_tenants (tenant_id, company_name, gstn, plan_tier, is_active)
           VALUES (
               %s, %s,
               CASE WHEN EXISTS (SELECT 1 FROM dim_tenants WHERE gstn = %s::char(15))
                    THEN NULL ELSE %s::char(15) END,
               'starter', true
           )
           ON CONFLICT (tenant_id) DO NOTHING""",
        (tid, TENANT_NAME, TENANT_GSTN, TENANT_GSTN),
    )


def _upsert_supplier(cur, vendor: dict, tid: str, bank_details: dict | None = None):
    name = _s(vendor.get("name"))
    if not name:
        return None
    gstn = _gstn_clean(vendor.get("gstin") or vendor.get("gstn"))
    source_supplier_id = gstn or re.sub(r"\s+", "_", name.upper())[:100]
    bd = bank_details or {}
    bank_account = _s(bd.get("account_no") or bd.get("account_number"))
    bank_ifsc    = _s(bd.get("ifsc"))
    bank_name    = _s(bd.get("bank_name"))

    # 1. Try to match an existing vendor by GSTN — check both dim_suppliers.gstn and
    #    supplier_gstns (for vendors with multiple GSTNs). Prevents duplicates when the
    #    same vendor was created manually and a bill is later uploaded for them.
    if gstn:
        cur.execute(
            """SELECT s.supplier_id FROM dim_suppliers s
               WHERE s.tenant_id = %s AND s.gstn = %s::char(15) AND s.is_active = true
               UNION
               SELECT sg.supplier_id FROM supplier_gstns sg
               WHERE sg.tenant_id = %s AND sg.gstn = %s::char(15)
               LIMIT 1""",
            (tid, gstn, tid, gstn),
        )
        row = cur.fetchone()
        if row:
            matched_id = str(row[0])
            cur.execute(
                """UPDATE dim_suppliers SET
                       name         = %s,
                       gstn         = COALESCE(gstn, %s::char(15)),
                       email        = COALESCE(%s, email),
                       bank_account = COALESCE(%s, bank_account),
                       bank_ifsc    = COALESCE(%s, bank_ifsc),
                       bank_name    = COALESCE(%s, bank_name),
                       is_active    = true
                   WHERE supplier_id = %s AND tenant_id = %s""",
                (name, gstn, _s(vendor.get("email")), bank_account, bank_ifsc, bank_name,
                 matched_id, tid),
            )
            # Ensure this GSTN is recorded in supplier_gstns for future lookups
            cur.execute(
                """INSERT INTO supplier_gstns (tenant_id, supplier_id, gstn)
                   VALUES (%s, %s, %s::char(15))
                   ON CONFLICT (tenant_id, gstn) DO NOTHING""",
                (tid, matched_id, gstn),
            )
            return matched_id

    # 1b. No GSTN hit, but the PAN inside that GSTIN may belong to a vendor we
    #     already have. A company holds one PAN and a GSTIN per state, so a branch
    #     invoice arrives with an unseen GSTIN for a known entity — matching on GSTN
    #     alone forked it into a second vendor row with empty bank details.
    #
    #     Only VERIFIED vendors are adopted this way: PAN identifies the legal entity,
    #     but reusing an unverified record on that basis alone would silently glue
    #     together rows a human has not vouched for. The new GSTN is registered
    #     against the survivor and nothing on the master is overwritten — its
    #     name and bank details were verified, the document's were not.
    if gstn:
        eff_pan = gstn[2:12].upper()
        cur.execute(
            """SELECT supplier_id FROM dim_suppliers
               WHERE tenant_id = %s AND is_active = true AND status = 'VERIFIED'
                 AND upper(COALESCE(
                       CASE WHEN length(trim(gstn)) = 15
                            THEN substring(trim(gstn) FROM 3 FOR 10) END,
                       NULLIF(trim(pan), '')
                     )) = %s
               ORDER BY created_at ASC
               LIMIT 1""",
            (tid, eff_pan),
        )
        row = cur.fetchone()
        if row:
            matched_id = str(row[0])
            cur.execute(
                """INSERT INTO supplier_gstns (tenant_id, supplier_id, gstn)
                   VALUES (%s, %s, %s::char(15))
                   ON CONFLICT (tenant_id, gstn) DO NOTHING""",
                (tid, matched_id, gstn),
            )
            return matched_id

    # 2. Try to match by source_supplier_id within UPLOAD source (same file re-processed)
    cur.execute(
        """UPDATE dim_suppliers SET
               name = %s,
               gstn = COALESCE(%s::char(15), gstn),
               email = COALESCE(%s, email),
               bank_account = COALESCE(%s, bank_account),
               bank_ifsc    = COALESCE(%s, bank_ifsc),
               bank_name    = COALESCE(%s, bank_name),
               is_active = true
           WHERE tenant_id = %s AND source = 'UPLOAD' AND source_supplier_id = %s
           RETURNING supplier_id""",
        (name, gstn, _s(vendor.get("email")), bank_account, bank_ifsc, bank_name, tid, source_supplier_id),
    )
    row = cur.fetchone()
    if row:
        return str(row[0])

    # 3. No match — create a new UPLOAD vendor
    cur.execute(
        """INSERT INTO dim_suppliers
               (tenant_id, source_supplier_id, name, gstn, email, source, status, is_active,
                bank_account, bank_ifsc, bank_name)
           VALUES (%s, %s, %s, %s::char(15), %s, 'UPLOAD', 'UNVERIFIED', true, %s, %s, %s)
           RETURNING supplier_id""",
        (tid, source_supplier_id, name, gstn, _s(vendor.get("email")), bank_account, bank_ifsc, bank_name),
    )
    row = cur.fetchone()
    if row and gstn:
        new_id = str(row[0])
        cur.execute(
            """INSERT INTO supplier_gstns (tenant_id, supplier_id, gstn)
               VALUES (%s, %s, %s::char(15))
               ON CONFLICT (tenant_id, gstn) DO NOTHING""",
            (tid, new_id, gstn),
        )
        return new_id
    return str(row[0]) if row else None


def _store_invoice_pdf(cur, upload_id: str, tid: str, file_name: str, file_bytes: bytes) -> None:
    """Copy the invoice PDF to S3 and point the upload row at it. The bytes stay
    in file_data as the backup, so a failed upload loses nothing: the row keeps
    reading from the DB and scripts/migrate_docs_to_s3.py copies it later."""
    try:
        ref = object_store.save(object_store.key("invoices", tid, upload_id, file_name),
                                file_bytes, "application/pdf", local_backup=False)
    except Exception:
        _log.exception("S3 upload failed for invoice upload %s; it stays in the DB only", upload_id)
        return
    cur.execute("UPDATE raw_invoice_uploads SET storage_path = %s WHERE upload_id = %s", (ref, upload_id))


def _upsert_upload(cur, file_name: str, file_size: int, uploaded_by_name=None, uploaded_by_role=None, tid: str = None, file_bytes=None):
    import psycopg2
    _bin = psycopg2.Binary(file_bytes) if file_bytes else None
    cur.execute(
        """UPDATE raw_invoice_uploads
           SET file_size_bytes = %s, status = 'DONE',
               uploaded_by_name = COALESCE(%s, uploaded_by_name),
               uploaded_by_role = COALESCE(%s, uploaded_by_role),
               file_data = COALESCE(%s, file_data)
           WHERE tenant_id = %s AND file_name = %s
           RETURNING upload_id""",
        (file_size, uploaded_by_name, uploaded_by_role, _bin, tid, file_name),
    )
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute(
        """INSERT INTO raw_invoice_uploads
               (tenant_id, file_name, file_type, file_size_bytes, storage_path, status, ocr_engine_used,
                uploaded_by_name, uploaded_by_role, file_data)
           VALUES (%s, %s, 'PDF', %s, %s, 'DONE', 'NONE', %s, %s, %s)
           RETURNING upload_id""",
        (tid, file_name, file_size, f"uploads/{file_name}", uploaded_by_name, uploaded_by_role, _bin),
    )
    return str(cur.fetchone()[0])


def _insert_ocr_result(cur, upload_id: str, payload: dict, tid: str):
    cur.execute(
        """INSERT INTO raw_ocr_results
               (upload_id, tenant_id, engine, page_number, raw_response_jsonb, confidence_jsonb)
           VALUES (%s, %s, 'NONE', 1, %s, %s)
           RETURNING ocr_id""",
        (upload_id, tid, Json(payload), Json({"source": "gpt-4o-mini"})),
    )
    return str(cur.fetchone()[0])


def _upsert_bill(cur, payload: dict, supplier_id, upload_id, source_file: str, category=None, tid: str = None):
    from utils.ocr_engine import gst_amount

    taxes = payload.get("taxes") or {}
    bill_num = _s(payload.get("invoice_number")) or source_file
    # Falls back the way pipelines/upload_ocr.py already does. The two ingest
    # paths disagreed: this one took taxes.taxable_amount or nothing, so a bill
    # whose extraction filled `subtotal` but not `taxable_amount` — the same
    # number, under the other name — was stored with a subtotal of ZERO.
    taxable = (
        _num(taxes.get("taxable_amount"))
        or _num(payload.get("subtotal"))
        or 0
    )
    cgst = _num(taxes.get("cgst_amount")) or 0
    sgst = _num(taxes.get("sgst_amount")) or 0
    igst = _num(taxes.get("igst_amount")) or 0
    cess = _num(taxes.get("cess_amount")) or 0
    # What the document printed, or the parts added up — see gst_amount. An
    # invoice showing one combined "GST @ 18%" line and no split used to post
    # with zero tax, because summing was the only rule.
    tax_total = gst_amount(taxes)
    # Kept alongside the total rather than folded into it. The split is what
    # answers "intra-state or inter-state", what a GST return is filed from,
    # and what an ITC claim is checked against — and until these columns
    # existed it survived only inside raw_ocr_results' payload blob.
    cgst_rate = _num(taxes.get("cgst_rate"))
    sgst_rate = _num(taxes.get("sgst_rate"))
    igst_rate = _num(taxes.get("igst_rate"))
    discount = _num(payload.get("discount")) or 0
    total = _num(payload.get("total_amount"))
    gstn_seller = _gstn_clean((payload.get("vendor") or {}).get("gstin"))
    vendor_sap_code = _s(payload.get("vendor_sap_code")) or None
    service_month = _s(payload.get("service_month")) or None
    place_of_supply = _s(payload.get("place_of_supply")) or None
    source_bill_id = bill_num or source_file

    bill_date = _parse_date(payload.get("invoice_date"))
    due_date  = _parse_date(payload.get("due_date"))
    if not due_date and bill_date:
        due_date = _infer_due_date(bill_date, payload.get("payment_terms"))

    currency_code = _bill_currency(cur, payload, tid)
    # original_amount is always in the invoice's native currency; total is always INR
    # (conversion happens in staging_promotion for non-INR bills)
    original_amount = total

    # Try update first.
    #
    # This branch deliberately leaves the amounts alone: a bill already in
    # staging may carry corrections a human made in the app, and a re-upload
    # must not silently overwrite them.
    #
    # The GST split is the exception, and it has to be. Re-reading a document
    # is a statement about what the document says, and its tax lines are a fact
    # of the document — so when the payload carries a split it REPLACES what is
    # stored, components the document does not have included.
    #
    # COALESCE was wrong here, and wrong in a way that produced nonsense.
    # Accepting the Review Hub's "this should be IGST" correction clears
    # cgst/sgst and sets igst; re-uploading the same document then filled the
    # two NULLs from the invoice and left the corrected IGST in place, so the
    # bill carried CGST *and* SGST *and* IGST and its GST total was double the
    # truth. The correction is not lost by replacing it: the states have not
    # changed, so bill_coding proposes it again on the next scan, which is
    # where a disagreement with the document belongs.
    #
    # A payload with no split at all still leaves the stored one alone — that
    # is a re-ingest carrying less information, not a document that says the
    # tax is gone.
    split = (cgst, sgst, igst, cess, cgst_rate, sgst_rate, igst_rate)
    has_split = any(v for v in (cgst, sgst, igst, cess))

    sets = [
        "upload_id = COALESCE(%s, upload_id)", "sync_status = 'PENDING'",
        "category = COALESCE(%s, category)",
        "vendor_sap_code = COALESCE(%s, vendor_sap_code)",
        "service_month = COALESCE(%s, service_month)",
        "taxable_amount = COALESCE(%s, taxable_amount)",
        "place_of_supply = COALESCE(%s, place_of_supply)",
    ]
    params: list = [upload_id, category, vendor_sap_code, service_month,
                    taxable or None, place_of_supply]

    if has_split:
        sets += [
            "cgst_amount = %s", "sgst_amount = %s", "igst_amount = %s",
            "cess_amount = %s", "cgst_rate = %s", "sgst_rate = %s", "igst_rate = %s",
        ]
        params += [v or None for v in split]

    cur.execute(
        f"""UPDATE stg_bills SET {', '.join(sets)}
           WHERE tenant_id = %s AND source = 'UPLOAD' AND source_bill_id = %s
           RETURNING stg_bill_id, bill_date, due_date, subtotal, tax_amount,
                     total_amount, outstanding_amount, supplier_id, upload_id""",
        (*params, tid, source_bill_id),
    )
    row = cur.fetchone()
    if row:
        return row
    # Insert new
    cur.execute(
        """INSERT INTO stg_bills (
               tenant_id, source, source_bill_id, source_updated_at, upload_id,
               bill_number, bill_date, due_date, supplier_id, currency_code,
               subtotal, discount_amount, tax_amount, total_amount, original_amount,
               amount_paid, outstanding_amount, status, gstin_seller, validation_flag, sync_status,
               category, vendor_sap_code, service_month,
               taxable_amount, cgst_amount, sgst_amount, igst_amount, cess_amount,
               cgst_rate, sgst_rate, igst_rate, place_of_supply
           ) VALUES (
               %s, 'UPLOAD', %s, now(), %s,
               %s, %s, %s, %s, %s,
               %s, %s, %s, %s, %s,
               0, %s, 'DRAFT', %s::char(15), 'OK', 'PENDING',
               %s, %s, %s,
               %s, %s, %s, %s, %s,
               %s, %s, %s, %s
           )
           RETURNING stg_bill_id, bill_date, due_date, subtotal, tax_amount,
                     total_amount, outstanding_amount, supplier_id, upload_id""",
        (
            tid, source_bill_id, upload_id,
            bill_num, bill_date, due_date,
            supplier_id, currency_code,
            taxable, discount, tax_total, total, original_amount,
            total, gstn_seller,
            category, vendor_sap_code, service_month,
            taxable, cgst, sgst, igst, cess,
            cgst_rate, sgst_rate, igst_rate, place_of_supply,
        ),
    )
    return cur.fetchone()


def _upsert_fact_invoice(cur, bill_row, bill_num: str, source_file: str, category=None, tid: str = None, department: str = None, currency_code: str = "INR", original_amount=None):
    (stg_bill_id, bill_date, due_date, subtotal, tax_amount,
     total_amount, outstanding_amount, supplier_id, upload_id) = bill_row
    if original_amount is None:
        original_amount = total_amount

    total_amount, subtotal, tax_amount, outstanding_amount, fx_rate_id = _fx_convert(
        cur, currency_code, total_amount, subtotal, tax_amount, outstanding_amount,
        base_currency=_tenant_base_currency(cur, tid),
    )

    fy, fq, fm = _fiscal(bill_date, jurisdiction_for_cursor(cur, tid).fiscal_year_start_month if tid else 4)
    invoice_number = bill_num or source_file

    # Update if invoice_number already exists for this tenant (skip deleted/on_hold)
    cur.execute(
        """UPDATE fact_invoices SET
               invoice_date       = %s,
               due_date           = %s,
               subtotal           = %s,
               tax_amount         = %s,
               total_amount       = %s,
               outstanding_amount = %s,
               upload_id          = COALESCE(%s, upload_id),
               category           = COALESCE(%s, category),
               department         = COALESCE(%s, department),
               original_amount    = COALESCE(%s, original_amount),
               fx_rate_id         = COALESCE(%s::uuid, fx_rate_id),
               source_updated_at  = now()
           WHERE tenant_id = %s AND invoice_number = %s
             AND COALESCE(soft_status, 'active') = 'active'
           RETURNING invoice_id""",
        (bill_date, due_date, subtotal, tax_amount, total_amount,
         outstanding_amount, upload_id, category, department,
         original_amount, fx_rate_id, tid, invoice_number),
    )
    row = cur.fetchone()
    if row:
        return str(row[0]), True  # existing active record updated → duplicate

    # No active invoice — this is a fresh upload of a bill whose prior copy may sit in
    # the hold/deleted collection. Release the archived copy's staging link so the
    # partial unique index (tenant_id, stg_bill_id) doesn't collide on insert below.
    if stg_bill_id:
        cur.execute(
            """UPDATE fact_invoices SET
                   archived_stg_bill_id = COALESCE(archived_stg_bill_id, stg_bill_id),
                   stg_bill_id = NULL
               WHERE tenant_id = %s AND stg_bill_id = %s
                 AND COALESCE(soft_status, 'active') <> 'active'""",
            (tid, stg_bill_id),
        )

    # Insert new
    cur.execute(
        """INSERT INTO fact_invoices (
               tenant_id, invoice_type, stg_bill_id, supplier_id, upload_id,
               invoice_number, invoice_date, due_date,
               fiscal_year, fiscal_quarter, fiscal_month, currency_code,
               subtotal, tax_amount, total_amount, original_amount, amount_paid, outstanding_amount,
               status, source, source_updated_at, category, department, fx_rate_id
           ) VALUES (
               %s, 'AP', %s, %s, %s,
               %s, %s, %s,
               %s, %s, %s, %s,
               %s, %s, %s, %s, 0, %s,
               'DRAFT', 'UPLOAD', now(), %s, %s, %s::uuid
           )
           RETURNING invoice_id""",
        (tid, stg_bill_id, supplier_id, upload_id,
         invoice_number, bill_date, due_date, fy, fq, fm, currency_code,
         subtotal, tax_amount, total_amount, original_amount, outstanding_amount,
         category, department, fx_rate_id),
    )
    row = cur.fetchone()
    return (str(row[0]) if row else None), False  # new record → not duplicate


def _insert_line_items(cur, stg_bill_id: str, line_items: list, taxes: dict, tid: str = None):
    cgst_rate = _num(taxes.get("cgst_rate")) or 0
    sgst_rate = _num(taxes.get("sgst_rate")) or 0
    igst_rate = _num(taxes.get("igst_rate")) or 0
    tax_rate = cgst_rate + sgst_rate + igst_rate

    # Idempotent re-ingest: _upsert_bill reuses the same stg_bill_id when a bill
    # is re-uploaded, so clear this bill's existing lines before re-inserting —
    # otherwise every re-ingest APPENDS a second copy of every line (the cause of
    # doubled reconciliation rows).
    cur.execute("DELETE FROM stg_bill_line_items WHERE stg_bill_id = %s", (stg_bill_id,))

    for i, item in enumerate(line_items, start=1):
        # A line's pre-tax amount is Qty × Rate. Prefer that computed product over
        # the OCR-extracted amount, which occasionally has a fat-fingered digit /
        # decimal shift (e.g. a ₹2,02,131 line read as ₹20,21,311) even when qty and
        # rate are correct. Fall back to the OCR amount only when qty or rate is absent.
        qty = _num(item.get("quantity"))
        rate = _num(item.get("rate"))
        # A percentage-rate line prices differently: qty is the BASE amount and
        # rate is a percent of it, so the product has to be divided by 100. Left
        # as plain qty x rate it would inflate the line 100-fold — a 4% commission
        # on a ₹1,00,000 base would post as ₹4,00,000 instead of ₹4,000.
        rate_is_percent = bool(item.get("rate_is_percent"))
        if qty and rate and rate_is_percent:
            amount = round(qty * rate / 100, 2)
        elif qty and rate:
            amount = round(qty * rate, 2)
        else:
            amount = _num(item.get("amount")) or 0
        tax_amt = round(amount * tax_rate / 100, 4) if tax_rate else 0
        line_total = amount + tax_amt
        account_id = _s(item.get("account_id")) or _s(item.get("ledger_account_id"))
        sap_item_code = _s(item.get("sap_item_code"))
        sap_material_desc = _s(item.get("sap_material_desc"))
        sap_map_score = _num(item.get("sap_map_score"))
        sap_map_status = "AUTO" if sap_item_code else None
        service_month = _s(item.get("service_month"))
        cur.execute(
            """INSERT INTO stg_bill_line_items
                   (stg_bill_id, tenant_id, line_number, product_name, hsn_code,
                    quantity, unit, unit_price, discount_amount, tax_rate_percent,
                    tax_amount, line_total, account_id,
                    sap_item_code, sap_material_desc, sap_map_score, sap_map_status,
                    service_month, rate_is_percent)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (stg_bill_id, tid, i,
             _s(item.get("description")), _s(item.get("hsn_sac")),
             _num(item.get("quantity")), _s(item.get("unit")), _num(item.get("rate")),
             tax_rate, tax_amt, line_total, account_id,
             sap_item_code, sap_material_desc, sap_map_score, sap_map_status,
             service_month, rate_is_percent),
        )

def _log_ledger_mappings(cur, line_items: list, supplier_id, tid: str):
    """Record user-confirmed ledger choices so they feed the suggestion engine."""
    for item in line_items:
        account_id = _s(item.get("account_id")) or _s(item.get("ledger_account_id"))
        if not account_id:
            continue
        cur.execute(
            """INSERT INTO ledger_mapping_log
                   (tenant_id, supplier_id, hsn_code, description, account_id, source)
               VALUES (%s, %s, %s, %s, %s, 'user')""",
            (tid, supplier_id or None, _s(item.get("hsn_sac")),
             _s(item.get("description")), account_id),
        )
