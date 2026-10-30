# Communication Agent

| | |
|---|---|
| **Agent name** | `communication` |
| **Fleet card** | **None** — this is a shared utility, not a board card |
| **Priority** | 🔧 Utility — harden alongside the P0 agents |
| **Status** | 🟡 Partial — Gmail OAuth read/send, thread reading and outreach logging exist; there is no single gated, capped send path |
| **Default autonomy** | **L1** — render and draft; send behind a gate |
| **Difficulty** | Easy to build, easy to get wrong in a way customers see |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.10, §17 |
| **Last updated** | 2026-08-06 |

---

## 1. Why this doc exists

Communication is not on the fleet board, but **four agents cannot ship without it**:
[`payable`](PAYABLE_AGENT.md) (approval nudges, remittance advice),
[`receivable`](RECEIVABLE_AGENT.md) (dunning),
[`analytics`](ANALYTICS_AGENT.md) (digest), and
[`gst`](GST_AGENT.md) (vendor filing queries). Without one shared path, each would
implement its own sending, its own rate limiting, and its own idea of what is safe to
say to a vendor — which is how an agent fleet ends up emailing a customer four times
in an hour.

It is specced here as the fleet's single outbound door: **one send path, one cap, one
suppression list, one log.**

## 2. Purpose

Send the right message to the right counterparty, once, with a record of it — and
make it impossible for any agent to send outside those rules.

Existing material to build on: [`email_oauth.py`](../../backend_ocr/app/email_oauth.py)
and [`email_api.py`](../../backend_ocr/app/email_api.py) already do Gmail OAuth,
message listing, thread reading, thread summarization and per-contact sending;
`vendor_outreach_log` records outreach; `wf_nudges` drives approval nudges;
[`agent_api.py`](../../backend_ocr/app/agent_api.py) (the Vendor Bill agent) is the
precedent for thread-aware inbound handling.

## 3. Triggers

Only ever called by another agent, or directly by a user.

| Trigger | Type | Detail |
|---|---|---|
| Delegated send | `agent` | Payable, Receivable, Analytics, GST |
| User action | `user` | "Email this vendor" from a bill or vendor page |
| Reply received | `event` | Inbound reply on a tracked thread → notify the originating agent |
| Bounce / failure | `event` | Hard bounce → suppress the address and flag the contact |

## 4. Inputs

```json
{
  "template": "remittance_advice|approval_nudge|dunning_stage_2|vendor_query|report_digest",
  "recipient": {"contact_id": "uuid", "email": "…", "name": "…"},
  "context": {"bill_id": "…", "amount_inr": 390000, "invoice_no": "…"},
  "thread_ref": null,
  "attachments": [{"kind": "remittance_pdf", "ref": "…"}],
  "requested_by": {"agent": "payable", "run_id": "uuid"}
}
```

`requested_by` is mandatory. Every outbound message is attributable to the run that
asked for it.

## 5. Tools

| Tool | Source | Implementation |
|---|---|---|
| `render_template(template, context)` | **new** | Deterministic substitution into a tenant-owned template |
| `polish_copy(draft)` | **new** | Optional LLM pass for tone only — **may not change any figure, name, date or reference** |
| `check_sendable(recipient, template)` | **new** | Suppression list, caps, quiet hours, duplicate-in-window check |
| `send_email(...)` | **reuse** | Gmail send via [`email_oauth.py`](../../backend_ocr/app/email_oauth.py); the OTP mail in `disbursement_api._send_otp_email` is the existing SMTP precedent |
| `log_outreach(...)` | **wrap** | `vendor_outreach_log`, generalized to counterparties |
| `track_thread(thread_id)` | **reuse** | Thread reading already built in `email_api.py` |
| `classify_reply(message)` | **wrap** | Reuse the Vendor Bill agent's classifier pattern from [`agent_api.py`](../../backend_ocr/app/agent_api.py) |
| `suppress(email, reason)` | **new** | Bounce, unsubscribe, or manual request |

## 6. Steps

1. **Resolve the recipient** — contact record, verified email, preferred language.
2. **Render** the template deterministically. A missing required context field is a
   hard failure, not a blank in the email.
3. **Optional tone polish**, then **verify every substituted value survived
   unchanged.** Any drift → discard the polish and send the deterministic render.
   This is the mechanism that makes an LLM safe in the outbound path.
4. **Pre-send checks**, all blocking:
   - not on the suppression list;
   - within per-recipient cap (default 1 per 24 h per template family);
   - within per-tenant daily cap;
   - inside quiet hours (default 08:00–20:00 recipient-local);
   - no identical message sent in the dedupe window.
5. **Gate (L1).** The user sees the exact final text and recipient. At L2, low-risk
   templates auto-send and the rest still gate.
6. **Send**, with an idempotency key on `(run_id, template, recipient)` so a retried
   run cannot double-send.
7. **Log** to the outreach log and link to the requesting run.
8. **Track the thread**; on reply, classify and notify the originating agent.

## 7. Outcome schema

```json
{
  "template": "remittance_advice",
  "recipient": {"contact_id": "…", "email": "a***@vendor.com", "name": "…"},
  "subject": "Payment of ₹3,90,000 — Invoice INV-2026-06-114",
  "body_preview": "Dear …",
  "checks": {
    "suppressed": false,
    "recipient_cap": {"sent_24h": 0, "limit": 1, "ok": true},
    "tenant_cap": {"sent_today": 34, "limit": 200, "ok": true},
    "quiet_hours": {"ok": true},
    "duplicate_window": {"ok": true},
    "polish_verification": "passed"
  },
  "status": "sent",
  "message_id": "…",
  "thread_ref": "…",
  "sent_at": "2026-08-06T11:20:00+05:30",
  "idempotency_key": "…"
}
```

**Statuses:** `drafted` · `awaiting_approval` · `sent` · `blocked` (with the failing
check) · `failed` · `bounced`.

## 8. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: agent`, `trigger_ref: payable:<run_id>`, `reason: "Remittance advice after settlement"` |
| **WHOM** | `entity_type: vendor_contact`, `entity_id: <contact_id>` |
| **WHAT** | `action: "Sent remittance advice to a***@vendor.com"`, `outcome:` above |
| **HOW** | `tools_used: [render_template, check_sendable, send_email, log_outreach]`; steps log each check with its result |

Store the **subject and a body preview**, not the full body, plus a content hash so
"what exactly did we send?" is answerable from the message record without duplicating
customer correspondence into the audit store. Email addresses are masked
(`disbursement_api._mask_email` is the precedent).

## 9. Guardrails

This agent is the fleet's outbound blast radius, so its guardrails are the strictest:

- **Templates only.** No agent may send free-form text to an external party. Ever.
- **The LLM cannot change facts.** Post-polish verification of every substituted
  value, with discard-on-drift.
- **Caps at three levels** — per recipient, per template family, per tenant per day.
- **Suppression list is absolute**, including manual "do not contact".
- **Quiet hours** respected in the recipient's timezone.
- **Idempotency key** per `(run_id, template, recipient)`.
- **Gated by default.** Only explicitly low-risk templates may be raised to auto-send.
- **No attachments by default**; only from an allowlisted set (remittance advice,
  statement of account) and never anything containing bank credentials.
- **Reply-to routes to a human**, not to an agent inbox that nobody reads.
- **Kill switch** halts all fleet outbound immediately, per tenant.

**Known infrastructure risk (from [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §17):**
sending currently relies on a Gmail App Password for `admin@onsumm.ai`. That is
rate-limited, tied to one account, and will be the first thing to break under agent
volume. **Move to SES or SendGrid before enabling any auto-send**, or the caps above
will be enforced by Gmail throttling instead of by us — with silent failures.

## 10. Data model

**Reuses:** `vendor_outreach_log`, `supplier_contacts`, `dim_end_customer_contacts`,
`user_email_connections`, `wf_nudges`, `agent_email_events` (the Vendor Bill agent's
log).

**New:**

```sql
CREATE TABLE IF NOT EXISTS agent_messages_out (
    out_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL,
    run_id        uuid        REFERENCES agent_runs(run_id),
    requested_by  text        NOT NULL,        -- requesting agent name
    template      text        NOT NULL,
    contact_id    uuid,
    recipient     text        NOT NULL,        -- stored masked
    subject       text        NOT NULL,
    body_hash     text        NOT NULL,
    body_preview  text,
    status        text        NOT NULL DEFAULT 'drafted',
    blocked_by    text,                        -- which check blocked it
    message_id    text,
    thread_ref    text,
    idempotency_key text     UNIQUE,
    sent_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_msg_out_caps
    ON agent_messages_out (tenant_id, recipient, template, sent_at DESC);

CREATE TABLE IF NOT EXISTS comm_suppressions (
    suppression_id uuid       PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid       NOT NULL,
    email          text       NOT NULL,
    reason         text       NOT NULL,        -- bounce|unsubscribe|manual|complaint
    created_by     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS comm_templates (
    template_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL,
    name         text        NOT NULL,
    family       text        NOT NULL,         -- dunning|remittance|nudge|digest|query
    subject_tpl  text        NOT NULL,
    body_tpl     text        NOT NULL,
    required_ctx text[]      NOT NULL DEFAULT '{}',
    auto_send_ok boolean     NOT NULL DEFAULT false,
    is_active    boolean     NOT NULL DEFAULT true,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);
```

`required_ctx` is what turns a missing figure into a hard failure instead of an
email that says "Dear , your payment of  is due".

## 11. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/communication/run` | Render + (gated) send |
| `GET` | `/api/agents/communication/outbox` | Sent / drafted / blocked, filterable |
| `GET` | `/api/agents/communication/blocked` | Blocked messages with the failing check |
| `GET` | `/api/agents/communication/templates` | Templates |
| `PUT` | `/api/agents/communication/templates/{id}` | Tenant edits copy and `auto_send_ok` |
| `POST` | `/api/agents/communication/suppressions` | Suppress an address |
| `POST` | `/api/agents/communication/kill` | Halt all fleet outbound for the tenant |

## 12. Build plan

**Step 1 — template store + deterministic render (2 days).** `comm_templates`,
`required_ctx` enforcement. No sending yet.

**Step 2 — send path + outbox + idempotency (2 days).** One door, `agent_messages_out`,
reuse the existing Gmail send.

**Step 3 — checks: suppression, caps, quiet hours, dedupe (2 days).** Build these
*before* any agent is allowed to call the send path, not after.

**Step 4 — gate + approval UX (1 day).** Show the exact final text.

**Step 5 — tone polish + drift verification (1 day).** Optional; ship the
deterministic path first and treat polish as an enhancement.

**Step 6 — reply tracking + classification (2 days).** Reuse the Vendor Bill agent's
thread and classifier patterns.

**Step 7 — email infrastructure migration (2–3 days, separate).** SES/SendGrid,
SPF/DKIM/DMARC, bounce webhooks. **Prerequisite for any auto-send**, and worth
scheduling as its own piece of work rather than a step inside this agent.

**Total ≈ 10 days**, plus the infrastructure migration.

## 13. KPIs

| KPI | Target |
|---|---|
| Duplicate messages to the same recipient | **0** |
| Messages sent outside caps or quiet hours | **0** |
| Polish-drift incidents reaching a recipient | **0** |
| Bounce rate | < 2% |
| Delivery success | ≥ 98% |
| Complaints / unsubscribes | < 0.1% |

## 14. Open questions

1. **When does the SES/SendGrid migration happen?** It gates auto-send for the whole
   fleet. Until then every agent's outbound stays at L1 with a human on each send.
2. **Send from a tenant's own mailbox (their Gmail OAuth) or from a platform
   address?** Tenant mailbox has far better deliverability and reply behavior;
   platform address is simpler and looks like bulk mail.
3. **Who owns template copy** — Onsumm defaults, tenant-edited, or both? Proposal:
   ship defaults, allow tenant edits, keep `auto_send_ok` platform-controlled.
4. **Do replies feed back into the originating agent automatically**, or land in a
   human inbox first? Human-first is safer; agent-first is what makes follow-up
   loops actually close.
5. **Should the Vendor Bill agent's inbound path merge into this agent?** They share
   thread handling and classification; merging inbound and outbound into one
   communication surface may be cleaner than two half-overlapping implementations.
