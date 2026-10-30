"""Documents held against a vendor.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import quote
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from ..integrations.ocr import enforce_pdf_page_limit
from fastapi.responses import Response
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..core.vendor_activity_log import log_vendor_activity
import uuid as _uuid

from fastapi import APIRouter

router = APIRouter()

from ..core import object_store

# Reused so a claim approved from this document panel is validated and
# written exactly the way master_data_conflict.py's own finding actions
# would — no second copy of the PAN/GSTIN/IFSC/account-number rules, and no
# second copy of the writes themselves. `_resolve_single_valid_claim` /
# `_resolve_bank_claim` are pure (no DB, no async) despite living on an
# asyncpg-based adapter, so calling them from this psycopg2 router is safe.
from ..review.adapters.master_data_conflict import MasterDataConflictAdapter, _norm, _SINGLE_VALID_CONFIG
from .vendors import update_vendor, VendorUpdateRequest
from .vendor_gstins import add_supplier_gstin, AddGstinRequest
from .vendor_banking import add_vendor_bank_account, BankAccountBody
from .vendor_contacts import add_vendor_contact, ContactBody

DOC_TYPES = ("contract", "other")
_LABEL_MAX = 80


def _clean_type(doc_type: str | None, doc_label: str | None) -> tuple[str, str | None]:
    """A valid (doc_type, doc_label) pair, or 422.

    Contracts and IOs are the built-in type; anything else needs a name, which
    becomes a document type offered for every vendor.
    """
    t = (doc_type or "contract").strip().lower()
    if t not in DOC_TYPES:
        raise HTTPException(status_code=422, detail="doc_type must be 'contract' or 'other'")
    label = " ".join((doc_label or "").split())[:_LABEL_MAX] or None
    if t == "other" and not label:
        raise HTTPException(status_code=422, detail="Name the document type, e.g. Rate cards")
    return t, (label if t == "other" else None)


def _type_name(doc_type: str | None, doc_label: str | None) -> str:
    """What the Documents panel calls a type: 'Contract', or the team's own name."""
    if (doc_type or "contract") == "contract":
        return "Contract"
    return doc_label or "Other"


@router.get("/api/vendors/{supplier_id}/documents")
def list_vendor_documents(supplier_id: str, company_id: str = Query(default=None), brand_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Scope documents to the brand currently being viewed — so even a vendor shared
        # across brands shows a separate document set per brand, and migrating a vendor
        # never carries its documents along. Legacy rows with no brand (brand_id NULL,
        # e.g. uploaded before brand scoping) stay visible under every brand.
        cur.execute(
            """
            SELECT vd.id, vd.file_name, vd.file_size, vd.uploaded_by, vd.uploaded_at,
                   vd.doc_type, vd.doc_label, vd.claims_status
            FROM vendor_documents vd
            WHERE vd.supplier_id = %s AND vd.tenant_id = %s AND vd.deleted_at IS NULL
              AND (%s::uuid IS NULL OR vd.brand_id = %s::uuid OR vd.brand_id IS NULL)
            ORDER BY vd.uploaded_at DESC
            """,
            (supplier_id, tenant, brand_id, brand_id)
        )
        rows = cur.fetchall()
    put_conn(conn)
    def _s(v): return v.isoformat() if v else None
    return {"documents": [{
        "id": str(r["id"]), "file_name": r["file_name"], "file_size": r["file_size"],
        "uploaded_by": r["uploaded_by"], "uploaded_at": _s(r["uploaded_at"]),
        "doc_type": r["doc_type"] or "contract", "doc_label": r["doc_label"],
        "claims_status": r["claims_status"],
    } for r in rows]}


@router.get("/api/vendor-document-types")
def list_vendor_document_types():
    """Document types the team has named, across all vendors, most used first."""
    tenant = require_tenant()
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT doc_label AS label, COUNT(*) AS documents
            FROM vendor_documents
            WHERE tenant_id = %s AND doc_type = 'other' AND doc_label IS NOT NULL AND deleted_at IS NULL
            GROUP BY doc_label ORDER BY COUNT(*) DESC, doc_label
        """, (tenant,))
        rows = cur.fetchall()
    put_conn(conn)
    return {"types": [{"label": r["label"], "documents": int(r["documents"])} for r in rows]}


@router.post("/api/vendors/{supplier_id}/documents")
async def upload_vendor_document(supplier_id: str, request: Request, company_id: str = Query(default=None), brand_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    async with request.form() as form:
        file: UploadFile = form.get("file")
        uploaded_by: str = form.get("uploaded_by", "")
        doc_type, doc_label = _clean_type(form.get("doc_type"), form.get("doc_label"))
        if file is None:
            raise HTTPException(status_code=422, detail="file field is required")
        file_name = file.filename
        contents = await file.read()
    doc_id = store_vendor_document(tenant, supplier_id, brand_id, file_name, contents, uploaded_by, doc_type, doc_label)
    return {"id": doc_id, "file_name": file_name, "doc_type": doc_type, "doc_label": doc_label}


def store_vendor_document(tenant: str, supplier_id: str, brand_id: str | None, file_name: str,
                          contents: bytes, uploaded_by: str | None, doc_type: str, doc_label: str | None) -> str:
    """Write a file to the vendor's documents and return its id.

    Shared by upload and by filing an email attachment from the vendor inbox,
    so both land the same way: same storage, same brand stamping, and only a
    contract is queued to be read for values.
    """
    enforce_pdf_page_limit(contents, file_name or "")
    doc_id = str(_uuid.uuid4())
    dest = object_store.save(object_store.key("vendor-docs", tenant, supplier_id, doc_id), contents,
                             mimetypes.guess_type(file_name or "")[0])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Stamp the brand currently being viewed so the document stays with that brand —
            # it won't follow a migration and a shared vendor keeps a separate set per brand.
            # Fall back to the vendor's own brand when no active brand was supplied.
            if not brand_id:
                cur.execute("SELECT brand_id FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s", (supplier_id, tenant))
                brow = cur.fetchone()
                brand_id = brow[0] if brow else None
            # Only contracts are read for values. The INSERT wakes the extraction
            # worker, which claims PENDING rows, so other types go in as SKIPPED.
            cur.execute(
                "INSERT INTO vendor_documents (id, supplier_id, tenant_id, brand_id, file_name, file_path, file_size, uploaded_by, doc_type, doc_label, claims_status)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (doc_id, supplier_id, tenant, brand_id, file_name, dest, len(contents), uploaded_by or None,
                 doc_type, doc_label, "PENDING" if doc_type == "contract" else "SKIPPED")
            )
        conn.commit()
    finally:
        put_conn(conn)
    return doc_id


class DocTypeRequest(BaseModel):
    doc_type: str
    doc_label: str | None = None


@router.patch("/api/vendors/{supplier_id}/documents/{doc_id}/type")
def set_vendor_document_type(supplier_id: str, doc_id: str, body: DocTypeRequest):
    """Re-file a document. Making it a contract queues it to be read for values."""
    tenant = require_tenant()
    doc_type, doc_label = _clean_type(body.doc_type, body.doc_label)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT doc_type, doc_label, file_name, claims_status FROM vendor_documents
                WHERE id=%s AND supplier_id=%s AND tenant_id=%s AND deleted_at IS NULL
            """, (doc_id, supplier_id, tenant))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            # A document never read before is read now that it is a contract.
            # One already read keeps its result; reading it again is not free.
            requeue = doc_type == "contract" and row["claims_status"] == "SKIPPED"
            cur.execute("""
                UPDATE vendor_documents
                   SET doc_type=%s, doc_label=%s,
                       claims_status = CASE WHEN %s THEN 'PENDING' ELSE claims_status END
                 WHERE id=%s AND tenant_id=%s
            """, (doc_type, doc_label, requeue, doc_id, tenant))
            if requeue:
                cur.execute("SELECT pg_notify('ch_comm_extract', %s)", (doc_id,))
            old_name = _type_name(row["doc_type"], row["doc_label"])
            new_name = _type_name(doc_type, doc_label)
            if old_name != new_name:
                log_vendor_activity(
                    cur, supplier_id, "document_type_changed",
                    f"Re-filed {row['file_name'] or 'a document'} as {new_name.lower() if doc_type == 'contract' else new_name}",
                    changes=[{"field": "Document type", "from": old_name, "to": new_name}],
                    meta={"entity_id": doc_id}, tenant_id=tenant)
        conn.commit()
    finally:
        put_conn(conn)
    return {"id": doc_id, "doc_type": doc_type, "doc_label": doc_label, "queued": requeue}

@router.get("/api/vendors/{supplier_id}/documents/{doc_id}/download")
def download_vendor_document(supplier_id: str, doc_id: str, company_id: str = Query(default=None), preview: bool = Query(default=False),
                             signed: bool = Query(default=False)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT file_name, file_path FROM vendor_documents WHERE id=%s AND supplier_id=%s AND tenant_id=%s AND deleted_at IS NULL", (doc_id, supplier_id, tenant))
        row = cur.fetchone()
    put_conn(conn)
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    file_name, file_path = row
    if signed:
        link = object_store.signed_link_response(file_path)
        if link:
            return link
    # The row can outlive its file: a pre-S3 row stored on another server's
    # disk (a local API on the shared dev DB sees rows uploaded on the dev box)
    # or removed. That's a missing document, not a crash.
    content = object_store.load(file_path)
    if content is None:
        raise HTTPException(status_code=404, detail="This document's file isn't on this server")
    guessed_type, _ = mimetypes.guess_type(file_name)
    media_type = guessed_type or "application/octet-stream"
    disposition = "inline" if preview else "attachment"
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file_name or 'document')}"})

# Mirrors master_data_conflict.py's own finding_type-per-scope and
# anchor-claim conventions exactly, so a claim here maps onto the same
# rh_findings row that adapter would have written for it. Kept as a second
# copy rather than an import because the adapter runs under asyncpg and this
# router under psycopg2 — the mapping itself, not the DB access, is what's
# shared.
_FINDING_TYPE_FOR_SCOPE = {
    "vendor_pan": "vendor_pan_conflict",
    "vendor_gstin": "vendor_gstin_conflict",
    "payment_term": "payment_term_conflict",
    "vendor_bank": "vendor_bank_new",
    "vendor_contact": "vendor_contact_new",
}
# A bank/contact claim arrives as several sibling claims (account_number +
# ifsc + bank_name, or email + name + phone) sharing one finding, keyed off
# the anchor claim's index — same as the adapter's key_qualifier.
_ANCHOR_FIELD_FOR_SCOPE = {"vendor_bank": "account_number", "vendor_contact": "email"}


@router.get("/api/vendors/{supplier_id}/documents/{doc_id}/claims")
def get_vendor_document_claims(supplier_id: str, doc_id: str, company_id: str = Query(default=None)):
    """The Communication Agent's typed claims extracted from this contract
    document (backend_ocr/app/review/comm_extract.py::extract_claims_from_contract),
    for the same side-by-side "document here, extracted values there" review
    the bill-draft flow already gives a user — see comm_extraction_worker.py's
    contract path for how `claims_status` on vendor_documents and the row in
    `comm_extractions` get written.

    `claims_status` mirrors the extraction lifecycle even before an extraction
    row exists (PENDING = queued, not run yet), so the frontend can show
    "still processing" rather than an empty/broken state for a contract that
    landed moments ago.

    Each claim that master_data_conflict.py turned into an open Review Hub
    finding carries a `finding` object (`finding_id` + `actions`) so the
    document's own claims panel can offer the same `keep_new` approval the
    Review Hub does, without sending the admin there first. A claim with no
    `finding` is either already applied, failed validation, or not yet swept
    by the adapter.
    """
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT claims_status, claims_error FROM vendor_documents "
            " WHERE id=%s AND supplier_id=%s AND tenant_id=%s AND deleted_at IS NULL",
            (doc_id, supplier_id, tenant),
        )
        doc = cur.fetchone()
        if not doc:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Document not found")

        cur.execute(
            "SELECT extraction_id, model, prompt_version, claims, created_at "
            "  FROM comm_extractions "
            " WHERE tenant_id=%s AND source_type='contract' AND source_id=%s "
            " ORDER BY created_at DESC LIMIT 1",
            (tenant, doc_id),
        )
        extraction = cur.fetchone()

        claims = list(extraction["claims"] or []) if extraction else []
        finding_keys: dict[str, int] = {}
        if extraction:
            extraction_id = str(extraction["extraction_id"])
            for i, c in enumerate(claims):
                if not isinstance(c, dict):
                    continue
                finding_type = _FINDING_TYPE_FOR_SCOPE.get(c.get("scope"))
                if not finding_type:
                    continue
                anchor = _ANCHOR_FIELD_FOR_SCOPE.get(c.get("scope"))
                if anchor and c.get("field") != anchor:
                    continue
                finding_keys[f"{finding_type}:vendor:{supplier_id}:{extraction_id}:{i}"] = i

        if finding_keys:
            cur.execute(
                "SELECT finding_id::text, finding_key, actions_jsonb "
                "  FROM rh_findings "
                " WHERE tenant_id=%s AND finding_key = ANY(%s) AND resolved_at IS NULL",
                (tenant, list(finding_keys.keys())),
            )
            for row in cur.fetchall():
                idx = finding_keys.get(row["finding_key"])
                if idx is not None and isinstance(claims[idx], dict):
                    claims[idx]["finding"] = {
                        "finding_id": row["finding_id"], "actions": row["actions_jsonb"],
                    }
    put_conn(conn)

    return {
        "status": doc["claims_status"],
        "error": doc["claims_error"],
        "extraction": {
            "extraction_id": str(extraction["extraction_id"]),
            "model": extraction["model"],
            "prompt_version": extraction["prompt_version"],
            "created_at": extraction["created_at"].isoformat() if extraction["created_at"] else None,
            "claims": claims,
        } if extraction else None,
    }


def _vendor_snapshot(cur, supplier_id: str, tenant: str) -> dict | None:
    cur.execute(
        "SELECT s.name, s.pan, s.payment_terms_days, COALESCE(pg.gstin, s.gstn) AS gstin "
        "  FROM dim_suppliers s "
        "  LEFT JOIN LATERAL (SELECT gstin FROM supplier_gstins "
        "                      WHERE supplier_id = s.supplier_id AND tenant_id = s.tenant_id "
        "                        AND is_primary LIMIT 1) pg ON TRUE "
        " WHERE s.supplier_id = %s AND s.tenant_id = %s",
        (supplier_id, tenant),
    )
    row = cur.fetchone()
    return dict(row) if row else None


_APPROVABLE_SCOPES = ("vendor_pan", "vendor_gstin", "payment_term", "vendor_bank", "vendor_contact")


def _is_anchor_claim(claim: dict) -> bool:
    """True for the one claim per multi-claim scope that carries the action —
    a bank account's ifsc/bank_name or a contact's name/phone describe the
    anchor (account_number / email) rather than standing alone. Single-valid
    scopes (PAN/GSTIN/payment term) have no siblings, so any claim of theirs
    is its own anchor."""
    scope = claim.get("scope")
    if scope == "vendor_bank":
        return claim.get("field") in (None, "account_number")
    if scope == "vendor_contact":
        return claim.get("field") in (None, "email")
    return scope in _APPROVABLE_SCOPES


async def _resolve_and_apply_claim(cur, tenant: str, supplier_id: str, claims: list, index: int, vendor: dict, *, override: bool = False) -> tuple[str, str | None]:
    """Validate, and if warranted write, ONE anchor claim — mutating its (and
    any sibling's) status in `claims` in place. Returns (status, error). This
    is the one place the decision and the write happen; both the single-claim
    and approve-all endpoints below call it so there is exactly one copy.

    Reuses master_data_conflict.py's own validation (`_resolve_single_valid_claim`,
    `_resolve_bank_claim` — pure functions, no DB) and calls the exact same
    endpoints its `keep_new` finding action would (update_vendor,
    add_supplier_gstin, add_vendor_bank_account, add_vendor_contact) rather
    than a second copy of either. If the adapter has already turned this
    claim into a finding by the time this runs, that finding's next sweep
    simply sees the vendor row already matches and self-resolves.
    """
    claim = claims[index]
    scope = claim.get("scope")
    adapter = MasterDataConflictAdapter()
    error_message: str | None = None
    claim.pop("error", None)  # a fresh attempt owns its own outcome

    try:
        if scope in ("vendor_pan", "vendor_gstin", "payment_term"):
            resolved_status, new_value = adapter._resolve_single_valid_claim(scope, claim, vendor)
            if resolved_status == "CONFLICTED":
                cfg = _SINGLE_VALID_CONFIG[scope]
                current_value = vendor.get(cfg["field"])
                if current_value not in (None, "") and not override:
                    # A DIFFERENT value is already on file. Filling an empty
                    # field from a document is one thing; replacing a vendor's
                    # existing PAN/GSTIN/terms is a change of who we think we
                    # are paying, and a single click on a panel that approves
                    # everything at once is the wrong place to decide it. Leave
                    # the claim CONFLICTED so master_data_conflict.py's sweep
                    # raises it in the Review Hub, where the before/after diff,
                    # the "ask the vendor to confirm" action and the audit trail
                    # all already exist.
                    claim["status"] = "CONFLICTED"
                    # Records that a human did look at this value and confirm
                    # the document says it. master_data_conflict.py reads this
                    # to raise the finding as VERIFIED — without it the finding
                    # is treated as an unreviewed model guess and lane_for()
                    # files it under 'suspected', out of the compliance lane a
                    # vendor-identity conflict belongs in.
                    claim["verified_by"] = current_username() or "unknown"
                    # What "keep current" would keep. Its presence is also what
                    # tells the panel this is a replacement rather than an
                    # addition — see master_data_conflict.py's own stamp.
                    claim["current_value"] = f"{current_value}{cfg['unit']}"
                    error_message = (
                        f"Conflicts with the {cfg['label'].lower()} already on file "
                        f"({current_value}) — approve this change in the Review Hub."
                    )
                else:
                    if scope == "vendor_pan":
                        update_vendor(supplier_id, VendorUpdateRequest(pan=new_value))
                    elif scope == "vendor_gstin":
                        add_supplier_gstin(supplier_id, AddGstinRequest(gstin=new_value, make_primary=True))
                    else:
                        update_vendor(supplier_id, VendorUpdateRequest(payment_terms_days=int(new_value)))
                    claim["status"] = "APPLIED"
            else:
                claim["status"] = resolved_status  # APPLIED (already matched) or FAILED
                if resolved_status == "FAILED":
                    error_message = f"Couldn't read a usable {_SINGLE_VALID_CONFIG[scope]['label'].lower()} from this value."

        elif scope == "vendor_bank":
            cur.execute(
                "SELECT account_number FROM supplier_bank_accounts WHERE tenant_id=%s AND supplier_id=%s",
                (tenant, supplier_id),
            )
            existing_norm = {_norm(r["account_number"]) for r in cur.fetchall()}
            siblings = [c for c in claims if isinstance(c, dict) and c is not claim and c.get("scope") == "vendor_bank"]
            ifsc = next((c.get("new_value") for c in siblings if c.get("field") == "ifsc"), None)
            bank_name = next((c.get("new_value") for c in siblings if c.get("field") == "bank_name"), None)
            account, resolved_status = adapter._resolve_bank_claim(claim, ifsc, bank_name, existing_norm)
            if resolved_status == "CONFLICTED":
                await add_vendor_bank_account(supplier_id, BankAccountBody(
                    account_number=account, ifsc=(ifsc or "").strip().upper() or None,
                    bank_name=(bank_name or "").strip() or None,
                ))
                resolved_status = "APPLIED"
            claim["status"] = resolved_status
            for sib in siblings:
                if sib.get("status") in ("PENDING", "CONFLICTED"):
                    sib["status"] = resolved_status

        else:  # vendor_contact
            email = (claim.get("new_value") or "").strip().lower()
            siblings = [c for c in claims if isinstance(c, dict) and c is not claim and c.get("scope") == "vendor_contact"]
            if not email or "@" not in email:
                resolved_status = "FAILED"
            else:
                cur.execute(
                    "SELECT email FROM supplier_contacts "
                    " WHERE tenant_id=%s AND supplier_id=%s AND email IS NOT NULL",
                    (tenant, supplier_id),
                )
                existing = {(r["email"] or "").strip().lower() for r in cur.fetchall()}
                if email in existing:
                    resolved_status = "APPLIED"
                else:
                    name = next((c.get("new_value") for c in siblings if c.get("field") == "name"), None) or "Vendor contact"
                    phone = next((c.get("new_value") for c in siblings if c.get("field") == "phone"), None)
                    await add_vendor_contact(supplier_id, ContactBody(
                        name=name, role="Contract contact", email=email, phone=phone,
                    ))
                    resolved_status = "APPLIED"
            claim["status"] = resolved_status
            if resolved_status == "FAILED":
                error_message = "No usable email address in this contact — edit it and try again."
            for sib in siblings:
                if sib.get("status") in ("PENDING", "CONFLICTED"):
                    sib["status"] = resolved_status

    except HTTPException as exc:
        # The claim itself was well-formed; the endpoint that owns the write
        # refused it (a GSTIN whose PAN doesn't match the vendor's, a
        # duplicate, a validation rule). Keeping the real reason is the whole
        # difference between "couldn't read cleanly" — which this is NOT — and
        # a message that tells the user what to actually do about it.
        claim["status"] = "FAILED"
        error_message = str(exc.detail)

    # Persisted so a re-fetch (and the Review Hub's own copy of this claim)
    # still carries the reason. Without it the panel only ever knew "FAILED"
    # and had to guess, which is how a perfectly-read GSTIN ended up labelled
    # as unreadable.
    if error_message:
        claim["error"] = error_message

    return claim["status"], error_message


def _load_extraction_for_approval(cur, tenant: str, doc_id: str) -> dict:
    cur.execute(
        "SELECT extraction_id, claims FROM comm_extractions "
        " WHERE tenant_id=%s AND source_type='contract' AND source_id=%s "
        " ORDER BY created_at DESC LIMIT 1",
        (tenant, doc_id),
    )
    extraction = cur.fetchone()
    if not extraction:
        raise HTTPException(status_code=404, detail="No extraction found for this document")
    return extraction


@router.post("/api/vendors/{supplier_id}/documents/{doc_id}/claims/{index}/approve")
async def approve_document_claim(supplier_id: str, doc_id: str, index: int, company_id: str = Query(default=None), override: bool = False):
    """A human verifying ONE extracted claim ("yes, this is what the document
    actually says") — available the moment the claim is extracted, not
    gated on master_data_conflict.py's own periodic sweep having already
    turned it into a Review Hub finding.
    """
    tenant = require_tenant()
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            extraction = _load_extraction_for_approval(cur, tenant, doc_id)
        except HTTPException:
            put_conn(conn)
            raise

        claims = list(extraction["claims"] or [])
        if index < 0 or index >= len(claims) or not isinstance(claims[index], dict):
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Claim not found")
        claim = claims[index]

        if claim.get("status") not in ("PENDING", "CONFLICTED"):
            put_conn(conn)
            return {"status": claim.get("status"), "message": "Already resolved."}

        if not _is_anchor_claim(claim):
            put_conn(conn)
            raise HTTPException(status_code=400, detail="Approve the anchor claim, not a sibling field")
        if claim.get("scope") not in _APPROVABLE_SCOPES:
            put_conn(conn)
            raise HTTPException(status_code=400, detail=f"Claims of scope '{claim.get('scope')}' aren't approvable here")

        vendor = _vendor_snapshot(cur, supplier_id, tenant)
        if not vendor:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Vendor not found")

        status, error_message = await _resolve_and_apply_claim(cur, tenant, supplier_id, claims, index, vendor, override=override)

        # Deferred to the hub — wake the adapter sweep so it's actually there
        # to act on rather than appearing whenever the next poll lands.
        if status == "CONFLICTED":
            cur.execute("SELECT pg_notify('ch_review_adapters', '')")

        cur.execute(
            "UPDATE comm_extractions SET claims=%s::jsonb WHERE extraction_id=%s",
            (json.dumps(claims), extraction["extraction_id"]),
        )
    conn.commit()
    put_conn(conn)
    return {"status": status, "message": error_message}


@router.post("/api/vendors/{supplier_id}/documents/{doc_id}/claims/approve-all")
async def approve_all_document_claims(supplier_id: str, doc_id: str, company_id: str = Query(default=None)):
    """The single Approve button for a contract's whole claims panel —
    approves every still-open, approvable claim in one action instead of one
    row at a time. Runs the identical per-claim validation and write as
    `approve_document_claim` (`_resolve_and_apply_claim`), just looped over
    every anchor claim still at PENDING/CONFLICTED."""
    tenant = require_tenant()
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            extraction = _load_extraction_for_approval(cur, tenant, doc_id)
        except HTTPException:
            put_conn(conn)
            raise

        claims = list(extraction["claims"] or [])
        vendor = _vendor_snapshot(cur, supplier_id, tenant)
        if not vendor:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Vendor not found")

        eligible = [
            index for index, claim in enumerate(claims)
            if isinstance(claim, dict)
            and claim.get("status") in ("PENDING", "CONFLICTED")
            and _is_anchor_claim(claim)
            and claim.get("scope") in _APPROVABLE_SCOPES
        ]
        # PAN first, then GSTIN, then everything else. POST /gstins refuses a
        # GSTIN whose embedded PAN doesn't match the vendor's PAN on file, so
        # approving a matching PAN + GSTIN pair in extraction order (which puts
        # GSTIN first) failed the GSTIN on a PAN that was about to be corrected
        # one claim later. Resolving in dependency order makes the pair apply
        # cleanly in a single click.
        _ORDER = {"vendor_pan": 0, "vendor_gstin": 1}
        eligible.sort(key=lambda i: _ORDER.get(claims[i].get("scope"), 2))

        applied = 0
        failed = 0
        deferred = 0
        for index in eligible:
            status, _ = await _resolve_and_apply_claim(cur, tenant, supplier_id, claims, index, vendor)
            if status == "FAILED":
                failed += 1
            elif status == "APPLIED":
                applied += 1
                # Later claims in this same batch must see the write the
                # earlier one just committed — a GSTIN's PAN check reads
                # dim_suppliers live, so a stale snapshot here is what made
                # the pair fail before.
                vendor = _vendor_snapshot(cur, supplier_id, tenant) or vendor
            else:
                deferred += 1  # CONFLICTED — left for the Review Hub

        # A deferred conflict is only useful if it actually shows up in the
        # hub, so wake the adapter sweep now instead of letting the user wait
        # out its next poll. Same channel ReviewAdapterWorker LISTENs on.
        if deferred:
            cur.execute("SELECT pg_notify('ch_review_adapters', '')")

        cur.execute(
            "UPDATE comm_extractions SET claims=%s::jsonb WHERE extraction_id=%s",
            (json.dumps(claims), extraction["extraction_id"]),
        )
    conn.commit()
    put_conn(conn)
    return {"applied": applied, "failed": failed, "deferred": deferred}


@router.post("/api/vendors/{supplier_id}/documents/{doc_id}/claims/{index}/discard")
def discard_document_claim(supplier_id: str, doc_id: str, index: int, company_id: str = Query(default=None)):
    """"Keep what we already have" — the other half of a conflict's either/or.

    Answers the question rather than filing it away: the claim goes terminal
    (DISCARDED), so master_data_conflict.py stops asserting it and
    `sweep_autoresolve` closes the finding on the next pass, while the vendor
    record is left exactly as it was. The hub's generic dismiss could not say
    this — it suppresses the finding without recording that a human chose the
    value on file.
    """
    tenant = require_tenant()
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            extraction = _load_extraction_for_approval(cur, tenant, doc_id)
        except HTTPException:
            put_conn(conn)
            raise

        claims = list(extraction["claims"] or [])
        if index < 0 or index >= len(claims) or not isinstance(claims[index], dict):
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Claim not found")
        claim = claims[index]
        if claim.get("status") == "APPLIED":
            put_conn(conn)
            raise HTTPException(status_code=400, detail="Already applied — change it on the vendor record instead")

        claim["status"] = "DISCARDED"
        claim["error"] = None
        claim["decided_by"] = current_username() or "unknown"
        # Siblings describe the same thing, so they go with it.
        scope = claim.get("scope")
        if scope in ("vendor_bank", "vendor_contact"):
            for sib in claims:
                if (isinstance(sib, dict) and sib is not claim and sib.get("scope") == scope
                        and sib.get("status") in ("PENDING", "CONFLICTED")):
                    sib["status"] = "DISCARDED"

        cur.execute(
            "UPDATE comm_extractions SET claims=%s::jsonb WHERE extraction_id=%s",
            (json.dumps(claims), extraction["extraction_id"]),
        )
        # Let the sweep close the finding now rather than at its next poll.
        cur.execute("SELECT pg_notify('ch_review_adapters', '')")
    conn.commit()
    put_conn(conn)
    return {"status": claim["status"]}


class ClaimEditRequest(BaseModel):
    new_value: str


@router.patch("/api/vendors/{supplier_id}/documents/{doc_id}/claims/{index}")
def edit_document_claim(supplier_id: str, doc_id: str, index: int, body: ClaimEditRequest, company_id: str = Query(default=None)):
    """A human correcting what the OCR/LLM actually extracted, before
    approving it — the fix for a misread value, rather than only being able
    to accept or silently live with it. Resets the claim to PENDING so the
    next approve (single or all) re-validates the corrected value from
    scratch, exactly as if it had just been extracted this way. Only claims
    that haven't already been written (APPLIED) are editable — editing after
    the fact would silently diverge from what was actually written to the
    vendor record."""
    tenant = require_tenant()
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            extraction = _load_extraction_for_approval(cur, tenant, doc_id)
        except HTTPException:
            put_conn(conn)
            raise

        claims = list(extraction["claims"] or [])
        if index < 0 or index >= len(claims) or not isinstance(claims[index], dict):
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Claim not found")
        claim = claims[index]
        if claim.get("status") == "APPLIED":
            put_conn(conn)
            raise HTTPException(status_code=400, detail="Already applied — edit the vendor record directly instead")

        new_value = (body.new_value or "").strip()
        if not new_value:
            put_conn(conn)
            raise HTTPException(status_code=400, detail="Value can't be empty")
        claim["new_value"] = new_value
        claim["status"] = "PENDING"
        claim.pop("error", None)  # the reason belonged to the old value

        cur.execute(
            "UPDATE comm_extractions SET claims=%s::jsonb WHERE extraction_id=%s",
            (json.dumps(claims), extraction["extraction_id"]),
        )
    conn.commit()
    put_conn(conn)
    return {"status": claim["status"]}


@router.put("/api/vendors/{supplier_id}/documents/{doc_id}")
async def update_vendor_document(supplier_id: str, doc_id: str, request: Request, company_id: str = Query(default=None)):
    """Replace an Excel/CSV document with an edited version, recording the diff in vendor_doc_edits."""
    import json as _json
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    # Ensure audit table exists
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vendor_doc_edits (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                doc_id      UUID NOT NULL,
                tenant_id   UUID NOT NULL,
                edited_by   VARCHAR(100),
                edited_at   TIMESTAMPTZ DEFAULT now(),
                diff_json   JSONB NOT NULL
            )
        """)
    conn.commit()

    async with request.form() as form:
        file: UploadFile = form.get("file")
        edited_by: str = form.get("edited_by", "")
        diff_raw: str = form.get("diff", "[]")
        if file is None:
            raise HTTPException(status_code=422, detail="file field is required")
        contents = await file.read()
    enforce_pdf_page_limit(contents, file.filename or "")

    # Fetch existing record
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT file_path FROM vendor_documents WHERE id=%s AND supplier_id=%s AND tenant_id=%s AND deleted_at IS NULL", (doc_id, supplier_id, tenant))
        row = cur.fetchone()
    if not row:
        put_conn(conn)
        raise HTTPException(status_code=404, detail="Document not found")

    object_store.replace(row["file_path"], contents, file.content_type)

    # Update metadata
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE vendor_documents SET file_size=%s, uploaded_at=now() WHERE id=%s",
            (len(contents), doc_id)
        )
        # Insert audit row
        diff_data = _json.loads(diff_raw) if diff_raw else []
        cur.execute(
            "INSERT INTO vendor_doc_edits (doc_id, tenant_id, edited_by, diff_json) VALUES (%s,%s,%s,%s)",
            (doc_id, tenant, edited_by or None, psycopg2.extras.Json(diff_data))
        )
    conn.commit()
    put_conn(conn)
    return {"status": "updated"}

@router.get("/api/vendors/{supplier_id}/documents/{doc_id}/edits")
def get_vendor_document_edits(supplier_id: str, doc_id: str, company_id: str = Query(default=None)):
    """Return the edit history (audit log) for a document."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, edited_by, edited_at, diff_json FROM vendor_doc_edits WHERE doc_id=%s AND tenant_id=%s ORDER BY edited_at DESC",
            (doc_id, tenant)
        )
        rows = cur.fetchall()
    put_conn(conn)
    def _s(v): return v.isoformat() if v else None
    return {"edits": [{"id": str(r["id"]), "edited_by": r["edited_by"], "edited_at": _s(r["edited_at"]), "diff": r["diff_json"]} for r in rows]}

@router.delete("/api/vendors/{supplier_id}/documents/{doc_id}")
def delete_vendor_document(supplier_id: str, doc_id: str, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT file_path FROM vendor_documents WHERE id=%s AND supplier_id=%s AND tenant_id=%s AND deleted_at IS NULL", (doc_id, supplier_id, tenant))
        row = cur.fetchone()
        if row:
            # Soft delete: the row is hidden everywhere and the file stays in S3
            # as the record; only the disk copy goes.
            cur.execute("UPDATE vendor_documents SET deleted_at=now(), deleted_by=%s WHERE id=%s",
                        (current_username() or None, doc_id))
    conn.commit()
    put_conn(conn)
    if row:
        object_store.drop_local_copy(row[0])
    return {"status": "deleted"}
