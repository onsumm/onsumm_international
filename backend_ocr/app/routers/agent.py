"""
agent_api.py — the Vendor Bill Agent (first agent in the Agent Hub).

What it does, end to end:
  1. A background poller scans each connected mailbox every AGENT_POLL_INTERVAL
     seconds for new mail *from flagged vendor POCs* that has an attachment.
  2. No PDF attached  -> logged and skipped, NO LLM is ever called (manual only).
  3. PDF attached      -> subject+body go to an OpenAI classifier: bill or not?
  4. Classified a bill -> download the PDF, run the normal OCR pipeline, and drop
     the result into `agent_draft_bills` as a *pending draft* (NOT ingested, NOT
     in the approval chain). It surfaces in the Bills "Draft" tab where it reuses
     ReviewPage; submitting there runs the normal ingest + submit flow.

Every decision is written to `agent_email_events`, which powers the Agent Hub
activity log and also de-duplicates messages (a Gmail message id is processed
once per tenant).

The poller runs in its own daemon thread with its own event loop so blocking DB
/ Gmail calls never stall the API server's event loop.
"""
from __future__ import annotations

import asyncio
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from psycopg2.extras import Json, RealDictCursor
from pydantic import BaseModel

from ..integrations import email_oauth
from ..config import OPENAI_API_KEY, get_conn, put_conn
from ..security.auth import require_tenant
from ..integrations.ocr import process_pdf_async

router = APIRouter(tags=["Agent Hub"])

# Same on-disk store the OCR endpoints use, so drafts share the doc cache.
from ..core.storage import UPLOADS_DIR
from ..core import object_store

AGENT_POLL_INTERVAL = int(os.getenv("AGENT_POLL_INTERVAL", "600"))   # seconds (10 min)
AGENT_LOOKBACK      = os.getenv("AGENT_LOOKBACK", "7d")              # Gmail newer_than window
_CLASSIFY_MODEL     = os.getenv("AGENT_CLASSIFY_MODEL", "gpt-4o-mini")
# Below this the three-way document classifier's "contract" verdict is not
# trusted enough to divert the email out of the (unchanged) bill path — it
# falls through to classify_email_is_bill exactly as it always has.
_CONTRACT_CONFIDENCE_FLOOR = float(os.getenv("AGENT_CONTRACT_CONFIDENCE_FLOOR", "0.6"))


# ── Schema ───────────────────────────────────────────────────────────────────

def ensure_agent_tables():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            -- per-POC opt-in: the agent only watches contacts flagged here
            ALTER TABLE supplier_contacts   ADD COLUMN IF NOT EXISTS agent_enabled boolean NOT NULL DEFAULT false;
            -- when each mailbox was last scanned (shown in the hub)
            ALTER TABLE user_email_connections ADD COLUMN IF NOT EXISTS agent_last_scan_at timestamptz;

            -- global on/off per tenant
            CREATE TABLE IF NOT EXISTS agent_settings (
                tenant_id   uuid        PRIMARY KEY,
                enabled     boolean     NOT NULL DEFAULT true,
                updated_at  timestamptz NOT NULL DEFAULT now()
            );

            -- pending bills the agent OCR'd but has NOT ingested/submitted
            CREATE TABLE IF NOT EXISTS agent_draft_bills (
                id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id        uuid        NOT NULL,
                supplier_id      uuid,
                contact_id       uuid,
                thread_id        text,
                message_gmail_id text,
                from_email       text,
                subject          text,
                source_file      text        NOT NULL,
                ocr_payload      jsonb        NOT NULL,
                status           text        NOT NULL DEFAULT 'pending',
                created_at       timestamptz NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_adb_tenant_status ON agent_draft_bills (tenant_id, status);
            CREATE INDEX IF NOT EXISTS idx_adb_thread ON agent_draft_bills (tenant_id, thread_id);
            -- thread-aware duplicate/correction analysis
            ALTER TABLE agent_draft_bills ADD COLUMN IF NOT EXISTS analysis        text;  -- new | correction | duplicate
            ALTER TABLE agent_draft_bills ADD COLUMN IF NOT EXISTS suggestion      text;  -- human-facing note (corrections)
            ALTER TABLE agent_draft_bills ADD COLUMN IF NOT EXISTS related_invoice text;  -- prior invoice number it relates to

            -- append-only activity log + dedup of processed messages
            CREATE TABLE IF NOT EXISTS agent_email_events (
                id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id        uuid        NOT NULL,
                supplier_id      uuid,
                contact_id       uuid,
                message_gmail_id text        NOT NULL,
                thread_id        text,
                from_email       text,
                subject          text,
                action           text        NOT NULL,
                detail           text,
                draft_id         uuid,
                created_at       timestamptz NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, message_gmail_id)
            );
            CREATE INDEX IF NOT EXISTS idx_aee_tenant_time ON agent_email_events (tenant_id, created_at DESC);
        """)
        conn.commit()
        put_conn(conn)
        print("[startup] agent tables ready")
    except Exception:
        print("[startup] ERROR creating agent tables:")
        traceback.print_exc()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _agent_enabled(cur, tenant_id: str) -> bool:
    cur.execute("SELECT enabled FROM agent_settings WHERE tenant_id=%s", (tenant_id,))
    row = cur.fetchone()
    return True if row is None else bool(row["enabled"])   # default ON until toggled


def _live_access_token(conn, cur, row) -> str | None:
    """Live access token for a connection row, refreshing + persisting if expired.
    Returns None (rather than raising) so the poller can skip a broken mailbox."""
    exp = row["token_expires_at"]
    if exp and exp > datetime.now(timezone.utc):
        return email_oauth.decrypt_token(row["access_token"])
    refresh = email_oauth.decrypt_token(row.get("refresh_token"))
    if not refresh:
        return None
    try:
        fresh = email_oauth.refresh_access_token(refresh)
    except Exception:
        cur.execute("UPDATE user_email_connections SET status='error', updated_at=now() WHERE id=%s", (row["id"],))
        conn.commit()
        return None
    cur.execute(
        "UPDATE user_email_connections SET access_token=%s, token_expires_at=%s, status='active', updated_at=now() WHERE id=%s",
        (email_oauth.encrypt_token(fresh["access"]), fresh["expires_at"], row["id"]),
    )
    conn.commit()
    return fresh["access"]


_CLASSIFY_PROMPT = """You are an assistant for an accounts-payable / finance team.
You are given the SUBJECT and BODY of an email a vendor contact sent that has a PDF attached.
Decide whether the attached PDF is most likely a BILL / TAX INVOICE that AP should process and pay,
versus regular conversation or a non-invoice document (a quotation/estimate, purchase order, a signed
contract, a bank/account statement, marketing/brochure, a greeting, or a general reply).
Judge from the wording — invoices are announced with language like "please find attached our invoice",
"tax invoice", "bill for services", an invoice number, an amount due, payment terms, etc.
Respond as STRICT JSON: {"is_bill": true|false, "confidence": 0.0-1.0, "reason": "<one short sentence>"}."""


def classify_email_is_bill(subject: str, body: str) -> dict:
    if not OPENAI_API_KEY:
        return {"is_bill": False, "confidence": 0.0, "reason": "OpenAI key not configured"}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        content = f"SUBJECT: {subject or '(none)'}\n\nBODY:\n{(body or '')[:4000]}"
        resp = client.chat.completions.create(
            model=_CLASSIFY_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _CLASSIFY_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {
            "is_bill": bool(data.get("is_bill")),
            "confidence": float(data.get("confidence") or 0.0),
            "reason": str(data.get("reason") or ""),
        }
    except Exception as e:
        traceback.print_exc()
        return {"is_bill": False, "confidence": 0.0, "reason": f"classifier error: {e}"}


_CLASSIFY_DOC_PROMPT = """You are an assistant for an accounts-payable / finance team.
You are given the SUBJECT and BODY of an email a vendor contact sent that has a PDF attached.
Classify the attached PDF into exactly one of three kinds:
  "bill"     - a BILL / TAX INVOICE that AP should process and pay for a specific period —
               an invoice number, amount due, "tax invoice", "please find attached our invoice",
               payment terms on what is owed right now.
  "contract" - a signed or draft CONTRACT, service/master agreement, purchase order, rate card,
               statement of work, or amendment — a document that states commercial TERMS (rates,
               quantities, tenure, payment terms) the parties are agreeing to, rather than a bill
               for goods/services already rendered.
  "other"    - anything else: a quotation/estimate not yet agreed to, a bank/account statement,
               marketing/brochure, a greeting, or a general reply.
Judge from the wording, not just the filename. Respond as STRICT JSON:
{"kind": "bill"|"contract"|"other", "confidence": 0.0-1.0, "reason": "<one short sentence>"}."""


def classify_email_document(subject: str, body: str) -> dict:
    """Three-way sibling of `classify_email_is_bill`: bill | contract | other.

    This does NOT replace `classify_email_is_bill` as the function that decides
    the bill/OCR/draft path — `_scan_connection` still calls that, unchanged,
    for every message this classifier does not confidently call a contract.
    It only adds a way to notice a contract *before* that decision is made, so
    a genuine bill's treatment never depends on this classifier agreeing with
    the original one; a bill (or anything ambiguous) simply falls through to
    the exact code that has always handled it.
    """
    if not OPENAI_API_KEY:
        return {"kind": "other", "confidence": 0.0, "reason": "OpenAI key not configured"}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        content = f"SUBJECT: {subject or '(none)'}\n\nBODY:\n{(body or '')[:4000]}"
        resp = client.chat.completions.create(
            model=_CLASSIFY_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _CLASSIFY_DOC_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        kind = str(data.get("kind") or "other").lower()
        if kind not in ("bill", "contract", "other"):
            kind = "other"
        return {
            "kind": kind,
            "confidence": float(data.get("confidence") or 0.0),
            "reason": str(data.get("reason") or ""),
        }
    except Exception as e:
        traceback.print_exc()
        return {"kind": "other", "confidence": 0.0, "reason": f"classifier error: {e}"}


_DUP_PROMPT = """You are an accounts-payable assistant reviewing one vendor email thread (chain).
The vendor has just sent an invoice PDF (the "NEW invoice"). Earlier in the SAME thread you already
received one or more invoices (the "PRIOR invoices"). Using the full CONVERSATION text AND the invoice
details, decide the NEW invoice's relationship to the prior one(s):
- "duplicate": the same invoice already received, with no meaningful change (a resend / reminder /
  "gentle follow-up" with the identical bill attached). Same invoice number and same total.
- "correction": a revised or corrected version of a previously received invoice — an amount, tax, line
  item, date, or the invoice number changed, or the email says corrected / revised / updated / amended /
  "please ignore the previous invoice" / "use this instead".
- "new": a genuinely different invoice, unrelated to the prior ones.
Prefer "correction" over "duplicate" when the wording signals a replacement even if numbers look close.
Respond as STRICT JSON:
{"verdict":"duplicate|correction|new","related_invoice":"<prior invoice number, or ''>","reason":"<one short sentence>"}."""


def _thread_transcript(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        lines.append(
            f"[{m.get('date','')}] From: {m.get('from','')}\n"
            f"Subject: {m.get('subject','')}\n{m.get('body','')}"
        )
    return "\n\n---\n\n".join(lines)


def analyze_duplicate(transcript: str, new_summary: dict, priors: list[dict]) -> dict:
    """Judge whether the new invoice duplicates / corrects a prior one in the thread."""
    default = {"verdict": "new", "related_invoice": "", "reason": ""}
    if not OPENAI_API_KEY or not priors:
        return default
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        content = (
            f"CONVERSATION (oldest first):\n{(transcript or '')[:6000]}\n\n"
            f"NEW invoice (just attached):\n{json.dumps(new_summary, default=str)}\n\n"
            f"PRIOR invoices already received in this thread:\n{json.dumps(priors, default=str)}"
        )
        resp = client.chat.completions.create(
            model=_CLASSIFY_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _DUP_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        verdict = str(data.get("verdict") or "new").lower()
        if verdict not in ("duplicate", "correction", "new"):
            verdict = "new"
        return {
            "verdict": verdict,
            "related_invoice": str(data.get("related_invoice") or ""),
            "reason": str(data.get("reason") or ""),
        }
    except Exception:
        traceback.print_exc()
        return default


def _safe_draft_filename(filename: str, message_id: str) -> str:
    """Unique, path-safe name so agent PDFs never clobber each other on disk."""
    base = Path(filename or "attachment.pdf").name.replace("/", "_").replace("\\", "_")
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return f"agent_{message_id[:16]}_{base}"


def _claim_message(cur, conn, tenant_id: str, mid: str) -> str | None:
    """Atomically reserve a Gmail message for processing.

    Inserts a placeholder 'processing' event; the UNIQUE (tenant_id,
    message_gmail_id) constraint means only ONE scan can win the claim. Returns
    the event id if we won (caller processes it), or None if another scan/poll
    already claimed or finished it (caller skips). This is what prevents a manual
    Refresh and the background poll from both drafting the same bill."""
    cur.execute("""
        INSERT INTO agent_email_events (tenant_id, message_gmail_id, action)
        VALUES (%s, %s, 'processing')
        ON CONFLICT (tenant_id, message_gmail_id) DO NOTHING
        RETURNING id::text
    """, (tenant_id, mid))
    row = cur.fetchone()
    conn.commit()
    return row["id"] if row else None


def _release_claim(cur, conn, event_id: str) -> None:
    """Undo a claim so a later poll can retry (used on transient fetch errors)."""
    cur.execute("DELETE FROM agent_email_events WHERE id=%s", (event_id,))
    conn.commit()


async def _store_contract_document(cur, conn, tenant_id: str, poc: dict, msg: dict,
                                    mid: str, access: str, att: dict) -> str:
    """Download the attachment and store it exactly the way
    `POST /api/vendors/{id}/documents` (vendor_documents.py) does: same
    storage (object_store, vendor-docs/ key), same id-as-key convention, same table
    and column list — so a contract the agent detects looks, to every other
    part of the app, identical to one a human uploaded by hand. Returns the
    new vendor_documents.id.
    """
    import uuid as _uuid

    data = email_oauth.get_attachment(access, mid, att["attachment_id"])
    file_name = att.get("filename") or "contract.pdf"
    doc_id = str(_uuid.uuid4())
    dest = object_store.save(object_store.key("vendor-docs", tenant_id, poc["supplier_id"], doc_id),
                             data, "application/pdf")

    cur.execute(
        "SELECT brand_id FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s",
        (poc["supplier_id"], tenant_id),
    )
    brow = cur.fetchone()
    brand_id = brow["brand_id"] if brow else None

    cur.execute(
        """
        INSERT INTO vendor_documents
            (id, supplier_id, tenant_id, brand_id, file_name, file_path, file_size, uploaded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (doc_id, poc["supplier_id"], tenant_id, brand_id, file_name, dest,
         len(data), "Vendor Bill Agent"),
    )
    conn.commit()
    return doc_id


def _finalize_event(cur, event_id, poc, msg, action, draft_id, detail):
    """Fill in the claimed event row with the final outcome."""
    cur.execute("""
        UPDATE agent_email_events
        SET supplier_id=%s, contact_id=%s, thread_id=%s, from_email=%s,
            subject=%s, action=%s, detail=%s, draft_id=%s
        WHERE id=%s
    """, (poc["supplier_id"] if poc else None, poc["id"] if poc else None,
          msg.get("thread_id"), msg.get("from"), (msg.get("subject") or "")[:500],
          action, (detail or "")[:500], draft_id, event_id))


# ── Poller ───────────────────────────────────────────────────────────────────

def _prior_invoices_in_thread(cur, tenant_id, supplier_id, thread_id, new_invoice_number) -> list[dict]:
    """Invoices already seen that this new one might duplicate/correct:
    every prior agent draft in the SAME email thread, plus any already-ingested
    bill for this vendor carrying the same invoice number."""
    priors: list[dict] = []
    if thread_id:
        cur.execute("""
            SELECT ocr_payload, status, created_at
            FROM agent_draft_bills
            WHERE tenant_id=%s AND thread_id=%s
            ORDER BY created_at ASC
        """, (tenant_id, thread_id))
        for r in cur.fetchall():
            s = _draft_summary(r["ocr_payload"])
            priors.append({
                "source": f"agent_draft ({r['status']})",
                "invoice_number": s["invoice_number"],
                "invoice_date": s["invoice_date"],
                "total_amount": s["total_amount"],
            })
    if new_invoice_number:
        cur.execute("""
            SELECT invoice_number, status::text AS status, total_amount, invoice_date
            FROM fact_invoices
            WHERE tenant_id=%s AND supplier_id=%s AND invoice_number=%s
            LIMIT 5
        """, (tenant_id, supplier_id, new_invoice_number))
        for r in cur.fetchall():
            priors.append({
                "source": f"submitted_bill ({r['status']})",
                "invoice_number": r["invoice_number"],
                "invoice_date": str(r["invoice_date"]) if r["invoice_date"] else None,
                "total_amount": float(r["total_amount"]) if r["total_amount"] is not None else None,
            })
    return priors


async def _scan_connection(c: dict) -> None:
    tenant_id = str(c["tenant_id"])
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if not _agent_enabled(cur, tenant_id):
            return
        cur.execute("""
            SELECT id::text, supplier_id::text, name, email
            FROM supplier_contacts
            WHERE tenant_id=%s AND agent_enabled=true AND email IS NOT NULL AND email <> ''
        """, (tenant_id,))
        pocs = cur.fetchall()
        if not pocs:
            return

        # Reopen claims left dangling by a crash mid-processing so they retry.
        cur.execute("""
            DELETE FROM agent_email_events
            WHERE tenant_id=%s AND action='processing' AND created_at < now() - interval '30 minutes'
        """, (tenant_id,))
        conn.commit()

        access = _live_access_token(conn, cur, c)
        if not access:
            return

        froms = " OR ".join(f"from:{p['email']}" for p in pocs)
        query = f"({froms}) has:attachment newer_than:{AGENT_LOOKBACK}"
        try:
            msg_ids = email_oauth.list_messages(access, query, max_results=25)
        except Exception:
            traceback.print_exc()
            return

        for mid in msg_ids:
            # Claim the message up-front. If we don't win the claim, another scan
            # (e.g. a manual Refresh) already handled it — skip, no duplicate.
            event_id = _claim_message(cur, conn, tenant_id, mid)
            if not event_id:
                continue

            try:
                msg = email_oauth.get_message(access, mid)
            except Exception:
                _release_claim(cur, conn, event_id)   # transient — retry next poll
                continue

            frm = (msg.get("from") or "").lower()
            poc = next((p for p in pocs if (p["email"] or "").lower() in frm), None)
            if not poc:
                _finalize_event(cur, event_id, None, msg, "skipped_no_poc", None, "Sender not a watched POC")
                conn.commit()
                continue

            # Persist the inbound POC email body (previously fetched then discarded)
            # so chain instructions like "pay 30% before <date>" survive, then enrich
            # the fact graph off the request path. Runs for every watched-POC email,
            # not just bills, since a plain text email can carry crucial terms.
            try:
                cur.execute("""
                    INSERT INTO poc_email_messages
                        (tenant_id, supplier_id, contact_id, direction, gmail_msg_id, thread_id,
                         from_email, subject, body)
                    VALUES (%s,%s,%s,'inbound',%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id, gmail_msg_id, direction) WHERE gmail_msg_id IS NOT NULL
                    DO UPDATE SET body=EXCLUDED.body, subject=EXCLUDED.subject
                    RETURNING message_id::text
                """, (tenant_id, poc["supplier_id"], poc["id"], mid, msg.get("thread_id"),
                      msg.get("from"), (msg.get("subject") or "")[:500], msg.get("body")))
                _pem = cur.fetchone()
                conn.commit()
                if _pem:
                    from ..domain import fact_graph
                    fact_graph.enqueue(fact_graph.enrich_email, tenant_id, _pem["message_id"])
            except Exception:
                conn.rollback()
                traceback.print_exc()

            pdfs = [a for a in (msg.get("attachments") or [])
                    if a.get("mime_type") == "application/pdf"
                    or (a.get("filename", "").lower().endswith(".pdf"))]

            # RULE: no PDF -> manual only, never call the LLM.
            if not pdfs:
                _finalize_event(cur, event_id, poc, msg, "skipped_no_pdf", None, "No PDF attached")
                conn.commit()
                continue

            # NEW: three-way pre-check for a contract, ahead of the (unchanged)
            # bill decision below. Only a confident "contract" verdict diverts
            # the message — anything else (bill, other, or low confidence)
            # falls straight through to classify_email_is_bill exactly as
            # before, so a genuine bill's path never depends on this new
            # classifier agreeing with the original one.
            doc_cls = classify_email_document(msg.get("subject", ""), msg.get("body", ""))
            if doc_cls["kind"] == "contract" and doc_cls["confidence"] >= _CONTRACT_CONFIDENCE_FLOOR:
                att = pdfs[0]
                try:
                    doc_id = await _store_contract_document(cur, conn, tenant_id, poc, msg, mid, access, att)
                except Exception as e:
                    _finalize_event(cur, event_id, poc, msg, "contract_store_failed", None, str(e)[:200])
                    conn.commit()
                    continue
                _finalize_event(cur, event_id, poc, msg, "contract_detected", doc_id, doc_cls["reason"])
                conn.commit()
                continue

            cls = classify_email_is_bill(msg.get("subject", ""), msg.get("body", ""))
            if not cls["is_bill"]:
                _finalize_event(cur, event_id, poc, msg, "skipped_not_bill", None, cls["reason"])
                conn.commit()
                continue

            # It's a bill -> download, OCR, and stash as a pending draft.
            att = pdfs[0]
            try:
                data = email_oauth.get_attachment(access, mid, att["attachment_id"])
                safe = _safe_draft_filename(att.get("filename", "invoice.pdf"), mid)
                (UPLOADS_DIR / safe).write_bytes(data)
                if object_store.enabled():
                    # The staged copy is what OCR and a later submit read; the
                    # S3 copy is what survives the disk until it is submitted.
                    try:
                        object_store.save(object_store.key("agent-drafts", tenant_id, safe), data, "application/pdf",
                                          local_backup=False)
                    except Exception:
                        traceback.print_exc()
                # The mailbox's tenant decides which invoices the OCR prompt expects.
                from ..core.jurisdiction import get_jurisdiction
                from utils.ocr_engine import set_ocr_jurisdiction
                _j = get_jurisdiction(tenant_id)
                set_ocr_jurisdiction(_j.profile, _j.country_code)
                ocr = await process_pdf_async(str(UPLOADS_DIR / safe))
                ocr["_source_file"] = safe
            except Exception as e:
                _finalize_event(cur, event_id, poc, msg, "ocr_failed", None, str(e)[:200])
                conn.commit()
                continue

            # Thread-aware duplicate / correction check: if this same chain already
            # produced a bill (draft or submitted), read the WHOLE thread and let the
            # LLM decide whether this new PDF duplicates or corrects it.
            new_summary = _draft_summary(ocr)
            priors = _prior_invoices_in_thread(cur, tenant_id, poc["supplier_id"],
                                               msg.get("thread_id"), new_summary["invoice_number"])
            verdict = {"verdict": "new", "related_invoice": "", "reason": ""}
            if priors:
                try:
                    transcript = _thread_transcript(email_oauth.get_thread(access, msg.get("thread_id")))
                except Exception:
                    transcript = f"Subject: {msg.get('subject','')}\n{msg.get('body','')}"
                verdict = analyze_duplicate(transcript, new_summary, priors)

            # Pure duplicate -> do NOT create a second draft; log it so the human sees it.
            if verdict["verdict"] == "duplicate":
                rel = verdict.get("related_invoice") or new_summary["invoice_number"] or "?"
                _finalize_event(cur, event_id, poc, msg, "skipped_duplicate", None,
                                f"Duplicate of invoice {rel} — {verdict.get('reason','')}")
                conn.commit()
                continue

            suggestion = None
            if verdict["verdict"] == "correction":
                rel = verdict.get("related_invoice") or "a previously received invoice"
                suggestion = (f"This looks like a correction of invoice {rel}. {verdict.get('reason','')} "
                              f"If that bill was already submitted, consider deleting it and submitting "
                              f"this corrected version; otherwise discard the older draft.")

            cur.execute("""
                INSERT INTO agent_draft_bills
                    (tenant_id, supplier_id, contact_id, thread_id, message_gmail_id, from_email, subject,
                     source_file, ocr_payload, analysis, suggestion, related_invoice)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id::text
            """, (tenant_id, poc["supplier_id"], poc["id"], msg.get("thread_id"), mid,
                  msg.get("from"), (msg.get("subject") or "")[:500], safe, Json(ocr),
                  verdict["verdict"], suggestion, verdict.get("related_invoice") or None))
            draft_id = cur.fetchone()["id"]
            _finalize_event(cur, event_id, poc, msg, "draft_created", draft_id,
                            suggestion or cls["reason"])
            conn.commit()

        cur.execute("UPDATE user_email_connections SET agent_last_scan_at=now() WHERE id=%s", (c["id"],))
        conn.commit()
    finally:
        put_conn(conn)


async def _scan_conns(conns: list) -> None:
    for c in conns:
        try:
            await _scan_connection(c)
        except Exception:
            traceback.print_exc()


async def _run_cycle() -> None:
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM user_email_connections WHERE status='active'")
        conns = cur.fetchall()
    finally:
        put_conn(conn)
    await _scan_conns(conns)


async def scan_tenant_now(tenant_id: str) -> None:
    """Scan just one tenant's mailboxes right now (manual 'Refresh')."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM user_email_connections WHERE status='active' AND tenant_id=%s", (tenant_id,))
        conns = cur.fetchall()
    finally:
        put_conn(conn)
    await _scan_conns(conns)


async def _poller_loop() -> None:
    await asyncio.sleep(20)   # let startup migrations settle first
    print(f"[agent] poller started (every {AGENT_POLL_INTERVAL}s, lookback {AGENT_LOOKBACK})")
    while True:
        try:
            await _run_cycle()
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(AGENT_POLL_INTERVAL)


def start_agent_poller() -> None:
    """Launch the poller in a daemon thread with its own event loop."""
    import threading

    def _runner():
        try:
            asyncio.run(_poller_loop())
        except Exception:
            traceback.print_exc()

    threading.Thread(target=_runner, name="agent-mail-poller", daemon=True).start()


# ── Draft display helpers ─────────────────────────────────────────────────────

def _draft_summary(payload: dict) -> dict:
    payload = payload or {}
    vendor = payload.get("vendor") or {}
    return {
        "vendor_name": vendor.get("name") or None,
        "invoice_number": payload.get("invoice_number") or None,
        "invoice_date": payload.get("invoice_date") or None,
        "total_amount": payload.get("total_amount"),
        "currency": (payload.get("currency_code") or "INR"),
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/api/agent/status")
def agent_status(company_id: str = Query(default=None)):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        enabled = _agent_enabled(cur, company_id)
        cur.execute("SELECT COUNT(*) AS n FROM supplier_contacts WHERE tenant_id=%s AND agent_enabled=true", (company_id,))
        watched = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM agent_draft_bills WHERE tenant_id=%s AND status='pending'", (company_id,))
        drafts = cur.fetchone()["n"]
        cur.execute("SELECT MAX(agent_last_scan_at) AS t FROM user_email_connections WHERE tenant_id=%s", (company_id,))
        last_scan = cur.fetchone()["t"]
        cur.execute("SELECT COUNT(*) AS n FROM agent_email_events WHERE tenant_id=%s", (company_id,))
        events = cur.fetchone()["n"]
        put_conn(conn)
        return {
            "agent": "vendor_bill_agent",
            "enabled": enabled,
            "poll_interval": AGENT_POLL_INTERVAL,
            "watched_pocs": watched,
            "pending_drafts": drafts,
            "events_total": events,
            "last_scan_at": str(last_scan) if last_scan else None,
        }
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load agent status")


class ToggleBody(BaseModel):
    company_id: str
    enabled: bool


@router.post("/api/agent/toggle")
def agent_toggle(body: ToggleBody):
    body.company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_settings (tenant_id, enabled, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (tenant_id) DO UPDATE SET enabled=EXCLUDED.enabled, updated_at=now()
        """, (body.company_id, body.enabled))
        conn.commit()
        put_conn(conn)
        return {"enabled": body.enabled}
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to toggle agent")


class ScanBody(BaseModel):
    company_id: str


@router.post("/api/agent/scan")
async def agent_scan(body: ScanBody):
    """Run a scan of this tenant's mailboxes immediately and wait for it to finish.
    Runs in a worker thread (own event loop) so the API event loop isn't blocked
    by the OCR / Gmail calls."""
    body.company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if not _agent_enabled(cur, body.company_id):
            put_conn(conn)
            return {"scanned": False, "reason": "agent_paused"}
        put_conn(conn)
    except Exception:
        if conn: put_conn(conn, close=True)

    try:
        await asyncio.to_thread(lambda: asyncio.run(scan_tenant_now(body.company_id)))
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Scan failed")
    return {"scanned": True}


@router.get("/api/agent/events")
def agent_events(company_id: str = Query(default=None), limit: int = Query(default=50)):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT e.id::text, e.supplier_id::text, e.contact_id::text, e.from_email,
                   e.subject, e.action, e.detail, e.draft_id::text, e.created_at, s.name AS supplier_name
            FROM agent_email_events e
            LEFT JOIN dim_suppliers s ON s.supplier_id = e.supplier_id
            WHERE e.tenant_id=%s
            ORDER BY e.created_at DESC
            LIMIT %s
        """, (company_id, min(max(limit, 1), 200)))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["created_at"] = str(r["created_at"])
        put_conn(conn)
        return rows
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load agent events")


@router.get("/api/agent/pocs")
def agent_pocs(company_id: str = Query(default=None)):
    """All vendor POCs with an email + their agent-watch flag, for hub management."""
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT c.id::text, c.supplier_id::text, c.name, c.role, c.email, c.agent_enabled, s.name AS supplier_name
            FROM supplier_contacts c
            LEFT JOIN dim_suppliers s ON s.supplier_id = c.supplier_id
            WHERE c.tenant_id=%s AND c.email IS NOT NULL AND c.email <> ''
            ORDER BY c.agent_enabled DESC, s.name ASC NULLS LAST, c.name ASC
        """, (company_id,))
        rows = [dict(r) for r in cur.fetchall()]
        put_conn(conn)
        return rows
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load contacts")


class ContactAgentBody(BaseModel):
    company_id: str
    enabled: bool


@router.patch("/api/vendors/{supplier_id}/contacts/{contact_id}/agent")
def toggle_contact_agent(supplier_id: str, contact_id: str, body: ContactAgentBody):
    body.company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE supplier_contacts SET agent_enabled=%s
            WHERE id=%s AND tenant_id=%s AND supplier_id=%s
        """, (body.enabled, contact_id, body.company_id, supplier_id))
        if cur.rowcount == 0:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Contact not found")
        conn.commit()
        put_conn(conn)
        return {"agent_enabled": body.enabled}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to update contact")


@router.get("/api/agent/drafts")
def list_drafts(company_id: str = Query(default=None)):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT d.id::text, d.supplier_id::text, d.contact_id::text, d.subject, d.from_email,
                   d.source_file, d.created_at, d.ocr_payload, d.analysis, d.suggestion,
                   d.related_invoice, s.name AS supplier_name
            FROM agent_draft_bills d
            LEFT JOIN dim_suppliers s ON s.supplier_id = d.supplier_id
            WHERE d.tenant_id=%s AND d.status='pending'
            ORDER BY d.created_at DESC
        """, (company_id,))
        out = []
        for r in cur.fetchall():
            summ = _draft_summary(r["ocr_payload"])
            out.append({
                "id": r["id"],
                "supplier_id": r["supplier_id"],
                "contact_id": r["contact_id"],
                "subject": r["subject"],
                "from_email": r["from_email"],
                "source_file": r["source_file"],
                "created_at": str(r["created_at"]),
                "vendor_name": summ["vendor_name"] or r["supplier_name"],
                "invoice_number": summ["invoice_number"],
                "invoice_date": summ["invoice_date"],
                "total_amount": summ["total_amount"],
                "currency": summ["currency"],
                "analysis": r["analysis"],
                "suggestion": r["suggestion"],
                "related_invoice": r["related_invoice"],
            })
        put_conn(conn)
        return out
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load drafts")


@router.get("/api/agent/drafts/{draft_id}")
def get_draft(draft_id: str, company_id: str = Query(default=None)):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id::text, supplier_id::text, source_file, subject, from_email, ocr_payload,
                   analysis, suggestion, related_invoice
            FROM agent_draft_bills
            WHERE id=%s AND tenant_id=%s AND status='pending'
        """, (draft_id, company_id))
        row = cur.fetchone()
        put_conn(conn)
        if not row:
            raise HTTPException(status_code=404, detail="Draft not found")
        return {
            "id": row["id"],
            "supplier_id": row["supplier_id"],
            "source_file": row["source_file"],
            "subject": row["subject"],
            "from_email": row["from_email"],
            "payload": row["ocr_payload"],
            "analysis": row["analysis"],
            "suggestion": row["suggestion"],
            "related_invoice": row["related_invoice"],
        }
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load draft")


@router.get("/api/agent/drafts/{draft_id}/file")
def get_draft_file(draft_id: str, company_id: str = Query(default=None)):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT source_file FROM agent_draft_bills WHERE id=%s AND tenant_id=%s", (draft_id, company_id))
        row = cur.fetchone()
        put_conn(conn)
        if not row:
            raise HTTPException(status_code=404, detail="Draft not found")
        path = UPLOADS_DIR / row["source_file"]
        if path.exists():
            return FileResponse(str(path), media_type="application/pdf", filename=row["source_file"])
        data = None
        if object_store.enabled():
            data = object_store.load(f"s3://{object_store.S3_BUCKET}/"
                                     + object_store.key("agent-drafts", company_id, row["source_file"]))
        if data is None:
            raise HTTPException(status_code=404, detail="Draft file missing")
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{row["source_file"]}"'})
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to serve draft file")


class DraftStatusBody(BaseModel):
    company_id: str


@router.post("/api/agent/drafts/{draft_id}/submitted")
def mark_draft_submitted(draft_id: str, body: DraftStatusBody):
    """Called after the draft is ingested+submitted from ReviewPage."""
    return _set_draft_status(draft_id, require_tenant(), "submitted")


@router.delete("/api/agent/drafts/{draft_id}")
def discard_draft(draft_id: str, company_id: str = Query(default=None)):
    return _set_draft_status(draft_id, require_tenant(), "discarded")


def _set_draft_status(draft_id: str, company_id: str, status: str):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE agent_draft_bills SET status=%s WHERE id=%s AND tenant_id=%s",
                    (status, draft_id, company_id))
        if cur.rowcount == 0:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Draft not found")
        conn.commit()
        put_conn(conn)
        return {"status": status}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to update draft")
