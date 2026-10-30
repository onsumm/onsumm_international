# Implementation Plan — User-Sent Email (OAuth: Gmail / Microsoft)

| | |
|---|---|
| **Status** | Draft v1.0 — for review |
| **Owner** | Subhash Bishnoi |
| **Last updated** | 2026-07-18 |
| **Feature** | Let a logged-in user (e.g. Finance Controller) send emails to a vendor contact **from their own mailbox**, via OAuth, from inside the Contacts tab |
| **Related** | [PRD_AGENT_HUB.md](PRD_AGENT_HUB.md) (§13.10 Communication Agent), `vendor_outreach_log`, `sandbox_kyb.py` (token-cache precedent) |

---

## Table of contents
1. [Goal & scope](#1-goal--scope)
2. [Decisions taken](#2-decisions-taken)
3. [User identity model](#3-user-identity-model)
4. [OAuth architecture](#4-oauth-architecture)
5. [Provider setup (external prerequisite)](#5-provider-setup-external-prerequisite)
6. [Data model](#6-data-model)
7. [Token security](#7-token-security)
8. [Backend: module & endpoints](#8-backend-module--endpoints)
9. [Send flow & logging](#9-send-flow--logging)
10. [Frontend: connect + compose](#10-frontend-connect--compose)
11. [Configuration](#11-configuration)
12. [Error handling & edge cases](#12-error-handling--edge-cases)
13. [Security considerations](#13-security-considerations)
14. [Phased rollout](#14-phased-rollout)
15. [Testing plan](#15-testing-plan)
16. [Relation to the Agent Hub](#16-relation-to-the-agent-hub)
17. [Open questions](#17-open-questions)

---

## 1. Goal & scope

**Goal.** From a vendor's **Contacts** tab, a logged-in user clicks *Email* on a
contact and sends a message that is delivered **from their own mailbox** (their
address in From, a copy in their Sent folder), using OAuth — not the shared
`admin@onsumm.ai` SMTP and not header spoofing.

**In scope**
- Per-user mailbox connection via OAuth (Gmail first, Microsoft next).
- Compose + send to a vendor contact from the Contacts tab.
- Logging every send to `vendor_outreach_log` for history + audit.

**Out of scope (now)**
- Inbound email / reply threading (only outbound send).
- Rich HTML editor (start with subject + plain/simple body).
- Bulk / campaign send (single contact at a time).
- The autonomous Communication Agent sending on its own (this feature is the
  human-driven precursor it will reuse).

---

## 2. Decisions taken
- **Mechanism:** OAuth connect (Gmail / Microsoft) — send via provider APIs on
  the user's behalf. *(chosen over per-user SMTP app passwords and Reply-To
  spoofing)*
- **Logging:** every send is recorded in `vendor_outreach_log`.
- **Provider order:** **Gmail-first**, Microsoft as a second adapter behind the
  same interface. *(pending final confirmation — see §17)*

---

## 3. User identity model

Users live in the `users` table, keyed by **(`company_id`, `username`)** with
`name`, `email`, `role`, `department`. `company_id` == `tenant_id`.

An email connection therefore belongs to **(`tenant_id`, `username`)**. The
compose/send endpoints already receive `company_id` + the acting username (as
other actions do via `actor_name` / `requested_by`), so no new auth plumbing is
required — we key the connection off the same pair.

---

## 4. OAuth architecture

Authorization Code flow (server-side token exchange), per user, per provider.

```
 ┌────────┐   1. Click "Connect Gmail"        ┌──────────────┐
 │  User  │ ────────────────────────────────▶ │  Hub UI      │
 └────────┘                                    └──────┬───────┘
     ▲                                                │ 2. GET /email-connection/authorize?provider=google
     │                                                ▼
     │                                        ┌──────────────┐  builds auth URL with
     │  3. redirect to Google consent         │  Backend     │  client_id, scope=gmail.send,
     │ ◀──────────────────────────────────────│  (authorize) │  state=signed(tenant,user,nonce)
     │                                         └──────────────┘
     │  4. user consents at Google
     ▼
 ┌────────────┐  5. redirect ?code=...&state=...  ┌──────────────┐
 │  Google    │ ─────────────────────────────────▶│  Backend     │  6. exchange code → access+refresh tokens
 └────────────┘   /api/oauth/email/callback        │  (callback)  │  7. fetch profile email, store connection
                                                    └──────┬───────┘  8. redirect back to app (success)
                                                           ▼
                                              user_email_connections row saved
```

**Send time:** load the user's connection → refresh the access token if expired
→ call the provider send API → log to `vendor_outreach_log`.

**State parameter** is a signed token (reuse `JWT_SECRET`) encoding
`{tenant_id, username, nonce, exp}` so the callback can't be forged or replayed
and we know which user is connecting. No session store needed.

---

## 5. Provider setup (external prerequisite)

These require **your** action; the app reads all values from env (§11).

### Google (Gmail API) — Phase 1
1. Google Cloud Console → new/existing project → **enable Gmail API**.
2. **OAuth consent screen**: choose **Internal** if every user is on the
   `onsumm.ai` Workspace (no Google verification review needed). Otherwise
   External + verification for the restricted `gmail.send` scope.
3. Create **OAuth Client ID** (type: Web application).
4. Authorized redirect URI: `https://<API_HOST>/api/oauth/email/callback`.
5. Scope: `https://www.googleapis.com/auth/gmail.send` (send-only; minimal).
6. Copy **Client ID + Client Secret** → env.

### Microsoft (Graph) — Phase 2
1. Azure Portal → **App registrations** → new registration (Web).
2. Redirect URI: same callback URL.
3. API permissions → Microsoft Graph → **Delegated** → `Mail.Send`
   (+ `offline_access` for refresh, `User.Read` for the address).
4. Create a **client secret** → env.

---

## 6. Data model

```sql
CREATE TABLE IF NOT EXISTS user_email_connections (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL,
    username       text        NOT NULL,          -- (tenant_id, username) = the user
    provider       text        NOT NULL,          -- 'google' | 'microsoft'
    email          text        NOT NULL,          -- the connected mailbox address
    access_token   text        NOT NULL,          -- encrypted at rest (§7)
    refresh_token  text,                           -- encrypted at rest
    token_expires_at timestamptz,
    scopes         text,
    status         text        NOT NULL DEFAULT 'active',  -- active | revoked | error
    connected_at   timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, username, provider)
);
CREATE INDEX IF NOT EXISTS idx_uec_user ON user_email_connections (tenant_id, username);
```

Auto-created on startup via an `_ensure_*` function + `@app.on_event("startup")`,
matching the `supplier_contacts` / `supplier_bank_accounts` pattern.

---

## 7. Token security

Tokens are bearer credentials to a user's mailbox — **treat as secrets**.
- **Encrypt at rest.** Encrypt `access_token` / `refresh_token` with
  `cryptography.fernet` using a new `EMAIL_TOKEN_KEY` env secret; store
  ciphertext, never plaintext.
- **Never return tokens** from any endpoint. Status endpoints return only
  `{connected, provider, email, status}`.
- **Never log tokens** (consistent with the SMTP-secret handling lesson).
- **Least scope**: `gmail.send` / `Mail.Send` only — no read/inbox access.
- **Revoke** on disconnect: call the provider revoke endpoint, then delete the
  row.

---

## 8. Backend: module & endpoints

New module `email_oauth.py` (provider-agnostic interface):
```
authorize_url(provider, state) -> str
exchange_code(provider, code) -> {access, refresh, expires_at, email, scopes}
refresh_token(provider, refresh) -> {access, expires_at}
send_email(provider, access_token, from_email, to, subject, body) -> message_id
  # Gmail:  POST gmail/v1/users/me/messages/send  (base64url RFC-822)
  # Graph:  POST /me/sendMail
```

New router `email_api.py`, `prefix="/api"`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/me/email-connection` | `{connected, provider, email, status}` for (tenant, username) |
| `GET` | `/api/me/email-connection/authorize` | `?provider=google` → `{auth_url}` (signed state) |
| `GET` | `/api/oauth/email/callback` | provider redirect target; exchanges code, stores connection, redirects to app |
| `DELETE` | `/api/me/email-connection` | revoke + delete connection |
| `POST` | `/api/vendors/{supplier_id}/contacts/{contact_id}/email` | compose+send via user's mailbox, then log |

Send-request body: `{ subject, body, company_id, username, cc? }`. `to` is
resolved server-side from the contact (prevents arbitrary send targets).

---

## 9. Send flow & logging

```
POST /api/vendors/{id}/contacts/{contact_id}/email
  1. resolve tenant from company_id / supplier
  2. load user_email_connections for (tenant, username); 409 if not connected
  3. resolve contact → to_email (must belong to this supplier/tenant)
  4. if token expired → refresh_token(); persist new access token
  5. send_email(provider, token, from=connection.email, to=to_email, subject, body)
  6. INSERT INTO vendor_outreach_log
        (supplier_id, tenant_id, requested_by, email_to, subject, body, status)
        VALUES (..., username, to_email, subject, body, 'sent')
  7. return { message_id, status: 'sent' }
  on send failure → log status='failed', return 502 with provider error
```

`vendor_outreach_log` already has every needed column — no schema change. The
existing `GET /api/vendors/{id}/outreach` endpoint then shows these sends in the
vendor's outreach history for free.

---

## 10. Frontend: connect + compose

**Contact card** — add an **Email** action (alongside the mailto chip; the
mailto stays as an "open in desktop client" escape hatch).

**Compose modal** (opened from Email):
- Header: To = contact name + email (read-only), From = user's connected mailbox.
- Fields: Subject, Body (textarea; simple formatting later).
- **Not connected yet?** The modal shows a *Connect your mailbox* step with
  **Connect Gmail** / **Connect Outlook** buttons instead of the composer.
  Clicking calls `/authorize`, opens the provider consent in a popup/redirect;
  on return the modal reloads connection status and switches to the composer.
- Send → `POST …/contacts/{id}/email` → toast success, close, refresh outreach.

**Connection status** is also surfaced in a small "Email: connected as
priya@onsumm.ai · Disconnect" strip (reusable later in a Settings page).

**OAuth return UX:** callback redirects to
`/app?email_connected=1&provider=google`; the app detects the param and toasts
"Mailbox connected."

---

## 11. Configuration

Add to `config.py` (all from env; no defaults for secrets):
```
GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT           # 'common' or your tenant
OAUTH_REDIRECT_URI      # https://<API_HOST>/api/oauth/email/callback
APP_RETURN_URL          # where callback sends the browser back to
EMAIL_TOKEN_KEY         # Fernet key for token encryption
```

---

## 12. Error handling & edge cases

| Case | Handling |
|---|---|
| User not connected | Send endpoint 409; UI shows Connect step |
| Access token expired | Auto-refresh; persist new token; retry once |
| Refresh token invalid/revoked | Mark `status='error'`; UI prompts reconnect |
| Consent denied at provider | Callback shows "connection cancelled", no row |
| Contact has no email | Email action disabled (email is mandatory anyway) |
| Send API failure | Log `status='failed'`; surface provider message; no partial state |
| User connects a different address than their `users.email` | Allowed; store the actual mailbox; optionally warn if mismatch |
| Multiple providers per user | `UNIQUE(tenant,user,provider)` allows one each; UI picks active |
| Token at rest | Encrypted (Fernet); decrypt only in-memory at send time |

---

## 13. Security considerations
- Send-only scopes; no inbox access requested.
- `to` never taken from the client — resolved from the stored contact, scoped to
  the tenant/supplier (prevents using a user's mailbox as an open relay).
- Signed, expiring OAuth `state` (JWT_SECRET) prevents CSRF/replay on callback.
- Tokens encrypted at rest, never logged, never returned.
- Tenant isolation: connections and contacts filtered by `tenant_id`.
- Rate limiting / daily send cap per user (config) to prevent abuse — reuse the
  cap concept from the Communication Agent guardrails.
- Audit: every send in `vendor_outreach_log` with `requested_by`.

---

## 14. Phased rollout

**Phase 1 — Gmail, end to end**
- `user_email_connections` table + token encryption helper.
- `email_oauth.py` Google adapter (authorize/exchange/refresh/send).
- Endpoints (status, authorize, callback, disconnect, send+log).
- Compose modal + Connect Gmail flow + outreach refresh.
- **Acceptance:** a Workspace user connects Gmail, emails a contact, the mail
  arrives from their address, a copy is in their Sent, and a `vendor_outreach_log`
  row exists.

**Phase 2 — Microsoft**
- Add Graph adapter behind the same interface; Connect Outlook button.

**Phase 3 — polish**
- Settings page for connections; templates; daily-cap enforcement; simple HTML
  body; surface send status in the outreach history UI.

---

## 15. Testing plan
- **Unit:** token encrypt/decrypt round-trip; state sign/verify; RFC-822 build
  for Gmail; refresh-on-expiry logic (mock provider).
- **Integration (post-credentials):** full connect flow on a test Workspace
  user; send to a seeded contact; assert mail received + `vendor_outreach_log`
  row + Sent-folder copy.
- **Negative:** expired token auto-refresh; revoked refresh → reconnect prompt;
  send to contact with wrong tenant → 404; denied consent.
- **Security:** confirm no token appears in logs/responses; `to` cannot be
  overridden by the client.

---

## 16. Relation to the Agent Hub

This is the **human-driven foundation** for the PRD's **Communication Agent**
(§13.10). The agent will reuse the exact same pieces:
- `user_email_connections` (or a system/service mailbox) for the sender,
- `email_oauth.send_email()` as a **tool**,
- `vendor_outreach_log` as the outcome/audit sink (WHOM/WHAT),
- vendor **contacts** as the recipient source.

Building it now as a user feature de-risks the agent later: same send path, same
logging, just a different trigger (agent vs user) and an approval gate.

---

## 17. Open questions
1. **Provider order** — confirm Gmail-first (recommended), or both together?
2. **Consent screen type** — are all users on the `onsumm.ai` Workspace (→
   Internal, no Google review), or external users too (→ verification needed)?
3. **Sender identity** — must the connected mailbox match the user's
   `users.email`, or may they connect any mailbox they own?
4. **Body format** — plain text first, or do we need HTML from day one?
5. **Daily send cap** — default per-user limit?
6. **Service mailbox** — for the future agent, send from each user's mailbox, or
   a shared tenant "service" mailbox connected once by an admin?

---

*Next step: resolve §17 (esp. Q2 consent type — it gates the Google app setup),
register the Google OAuth app, then implement Phase 1.*
