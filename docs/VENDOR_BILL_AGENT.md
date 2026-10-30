# Vendor Bill Agent

The first agent in the **Agent Hub**. It watches connected mailboxes for invoices
emailed by vendor points-of-contact (POCs), OCRs them, and drops them into the
Bills **Draft** tab for a human to review and submit — without ever auto-posting
a bill into the approval chain.

---

## What it does (end to end)

1. **Polls** each connected mailbox every `AGENT_POLL_INTERVAL` seconds (default
   **600 = 10 min**), and also on demand when a user hits **Refresh** in the Agent Hub.
2. Searches only for mail **from flagged POCs** that **has an attachment**
   (Gmail query: `(from:a@x OR from:b@y) has:attachment newer_than:7d`).
3. **No PDF attached → skipped, and the LLM is never called** (pure manual path).
4. **PDF attached →** the email subject + body are sent to an OpenAI classifier that
   decides *bill* vs *regular conversation*.
5. **Classified a bill →** the PDF is downloaded, run through the normal OCR
   pipeline, checked for duplicates/corrections (below), and stored as a
   **pending draft** in `agent_draft_bills`. It is **not** ingested and **not** in
   the approval chain.
6. Drafts appear in the Bills **Draft** tab, which **reuses `ReviewPage`** — the
   same editor as the upload flow. Editing + "Send for Approval" runs the normal
   `ingest → submit`, after which the draft is marked `submitted`.

Every decision is written to `agent_email_events`, which powers the Agent Hub
activity log **and** de-duplicates messages.

---

## Duplicate & correction handling (thread-aware)

A resent or corrected invoice arrives in the **same Gmail thread**. Before
creating a draft, the agent gathers any **prior invoices for that conversation**:

- every earlier agent draft in the **same `thread_id`**, and
- any already-submitted `fact_invoices` bill for that vendor with the **same
  invoice number**.

If priors exist, it reads the **entire thread transcript** and asks the LLM to
classify the new PDF against them:

| Verdict | Action |
|---|---|
| `duplicate` | **No new draft.** Logged as *"Skipped — duplicate invoice"* in the activity log. |
| `correction` | Draft created, flagged **CORRECTION**, with a human suggestion: consider deleting the existing bill and submitting this corrected one. **Suggestion only — the agent never deletes or submits.** |
| `new` | Normal draft. |

If there are no priors, the analysis (and its LLM call) is skipped entirely.

---

## Duplicate prevention (message-level)

Each Gmail message is **claimed atomically** before any work:
`INSERT ... ON CONFLICT (tenant_id, message_gmail_id) DO NOTHING` writes a
`processing` event row. Only one scan can win the claim, so a manual **Refresh**
and the background poll can never both draft the same message — even if they run
at the same instant. Safety valves:

- **Transient fetch error** → the claim is released so a later poll retries.
- **Crash mid-OCR** → `processing` claims older than 30 min are reaped at the
  start of each scan.

---

## Configuration

Per-POC opt-in: a contact is watched only when `supplier_contacts.agent_enabled = true`
(toggle it in the Agent Hub POC list or the vendor contacts UI). Global on/off per
tenant lives in `agent_settings.enabled` (default on).

Environment variables:

| Var | Default | Purpose |
|---|---|---|
| `AGENT_POLL_INTERVAL` | `600` | Seconds between background polls. |
| `AGENT_LOOKBACK` | `7d` | Gmail `newer_than` window per scan. |
| `AGENT_CLASSIFY_MODEL` | `gpt-4o-mini` | Model for the bill / duplicate classifiers. |
| `OPENAI_API_KEY` | — | Reused from the OCR pipeline. If unset, classifiers return "not a bill". |

Requires a connected Gmail mailbox with `gmail.readonly` scope (already granted by
the existing OAuth flow).

---

## Data model

| Table | Role |
|---|---|
| `supplier_contacts.agent_enabled` | Per-POC watch flag. |
| `user_email_connections.agent_last_scan_at` | Last scan time per mailbox. |
| `agent_settings` | `(tenant_id, enabled)` — global on/off. |
| `agent_draft_bills` | Pending OCR'd drafts + `analysis` / `suggestion` / `related_invoice`. |
| `agent_email_events` | Append-only activity log; `UNIQUE (tenant_id, message_gmail_id)` de-dupes. |

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agent/status` | Enabled flag, poll interval, watched POCs, pending drafts, last scan. |
| POST | `/api/agent/toggle` | Global on/off for the tenant. |
| POST | `/api/agent/scan` | Run a scan for this tenant now (manual Refresh). |
| GET | `/api/agent/events` | Activity log. |
| GET | `/api/agent/pocs` | All POCs with their watch flag. |
| PATCH | `/api/vendors/{sid}/contacts/{cid}/agent` | Toggle a POC's watch flag. |
| GET | `/api/agent/drafts` | Pending drafts (list). |
| GET | `/api/agent/drafts/{id}` | Draft OCR payload + analysis/suggestion. |
| GET | `/api/agent/drafts/{id}/file` | Draft PDF (for the ReviewPage preview). |
| POST | `/api/agent/drafts/{id}/submitted` | Mark a draft submitted (after ingest). |
| DELETE | `/api/agent/drafts/{id}` | Discard a draft. |

---

## Key files

- **Backend:** `backend_ocr/app/agent_api.py` (schema, classifier, duplicate
  analysis, poller, endpoints); `backend_ocr/app/email_oauth.py`
  (`list_messages`, `get_message`); registered + started in
  `backend_ocr/app/main.py`.
- **Frontend:** `frontend/src/api/agent.ts` (client);
  `frontend/src/pages/AgentHubPage.tsx` (status, toggles, POC list, activity log);
  `frontend/src/pages/BillsPage.tsx` (Draft tab); `frontend/src/App.tsx`
  (draft → ReviewPage handoff); `frontend/src/components/Sidebar.tsx` (nav).
