# ICICI CIB Payment API — Clarity Questions (Onsumm.ai Disbursement Integration)

**Context:** the guardrails in [`PAYMENT_GUARDRAILS.md`](../PAYMENT_GUARDRAILS.md) are **Onsumm.ai's own application-level controls**, defined and enforced by us — they are not something ICICI provides or is aware of. ICICI's job in this integration is to be a payment rail underneath those controls, not to duplicate or substitute for them. The purpose of this document is to confirm ICICI's documented API behavior is *compatible with and does not silently undermine* those controls, and to close the operational unknowns that affect how we build settlement/reconciliation on our side.

Source docs: [`ICICI_CIB_Payment_API_Technical_Documentation.md`](./ICICI_CIB_Payment_API_Technical_Documentation.md) (UAT technical spec) and [`PAYMENT_GUARDRAILS.md`](../PAYMENT_GUARDRAILS.md) (our enforcement model).

---

## Our guardrail model, for reference

Before any request reaches ICICI, Onsumm.ai already enforces (full detail in `PAYMENT_GUARDRAILS.md`):

- Payment initiation is **admin-role only** — no other role can trigger a disbursement.
- A single, emailed, SHA-256-hashed **OTP (120-second TTL, max 5 attempts)** is the one human authorization event per payment (or per bulk batch) — this is deliberately the *only* checkpoint between "admin clicked Pay Now" and "money moves."
- Beneficiary details are pulled from a GSTIN + KYC + bank-verified vendor profile automatically; any manual override requires a reason and is fully audit-logged (who/when/what changed).
- There is currently **no secondary approver role** for payments — Onsumm.ai's model is intentionally single-admin-plus-OTP, not maker-checker.

Anywhere ICICI's own workflow (maker-checker, step-up auth, additional OTP) would sit **on top of** this, it either needs to be turned off for our integration or explicitly reconciled with — a bank-side approval step we don't know about would silently change our security model without our knowledge.

---

## 1. Workflow-bypass compliance (highest priority)

The API's `WORKFLOW_REQD` field is documented as *"mandatory when bypassing the transaction workflow (no approval required)"* — this is the literal switch for CIB's own maker-checker.

1. When we bypass CIB's workflow, confirm the **only** authorization applied to the transaction is our authenticated, IP-whitelisted, certificate-encrypted API call — no additional CIB-side OTP, step-up, or human check is silently inserted or silently skipped that we're not accounting for.
2. Is workflow bypass a **per-transaction** flag we control on every request, or a **per-CIB-user/account** setting configured once at onboarding? We need to know whether a client could ever have some transactions silently routed through approval and others not, from the same integration.
3. Does bypassing CIB's approval workflow also bypass ICICI's own **fraud/AML screening**, or are those independent — i.e., does "no approval required" only mean "no human approver," while automated fraud checks still run? We need this stated explicitly, not inferred.
4. If our client's CIB account currently has multi-user maker-checker configured, does enabling API access for our integration **override** that configuration, or does the client's existing CIB workflow stay in effect unless we explicitly bypass it per request? Onsumm.ai needs to tell clients accurately which of the two is true.
5. Given Onsumm.ai's own control is a single admin-role OTP with no secondary approver — is that, on its own, an acceptable authorization model for ICICI to build against, or does ICICI require/recommend an additional bank-side control we're not currently told about for this class of integration (partner/aggregator, adhoc payee, IMPS)?

## 2. The "false success" behavior (§9.1 STP note)

Per the spec: under STP, the response only reflects the *account debit*. If the transaction later fails at RBI/NPCI/destination bank, the account is credited back manually — **but CIB is not updated and continues showing `SUCCESS`.** Our settlement logic (`PAYMENT_GUARDRAILS.md §7`) currently treats bank `SUCCESS` as terminal and marks the bill paid in the same transaction as OTP verification. Against this documented behavior, that's provably unsafe for STP transactions specifically.

6. Is there **any** machine-readable signal — webhook, later status transition, statement line — that surfaces a post-success failure and credit-back, or is a human support ticket genuinely the only way we'd learn about it?
7. What is the realistic window between debit and a failed-transaction credit-back completing? We need this to size a reconciliation job that re-checks settled bills rather than trusting the initial response permanently.
8. Is the "CIB may not reflect a later failure" behavior specific to STP, or does it also apply to workflow/approval-routed transactions?
9. Given this, should Onsumm.ai treat STP `SUCCESS` as **provisional** rather than terminal until a later statement/reconciliation check confirms it? If so, what's ICICI's recommended reconciliation cadence for a payments aggregator in our position?

## 3. Access & onboarding, per client

10. Confirm: each Onsumm.ai client (the actual ICICI current-account holder) individually needs (a) CIB transaction access — not view/fraud-scheme-only, (b) Adhoc Payment (PMT) access, and (c) one-time registration completed with authorization *inside CIB itself*. None of this is satisfiable by our aggregator-level credentials alone — correct?
11. If a client's CIB user access is later revoked, or the CIB ID goes dormant, does registration expire silently? Do we get any signal (API error code, webhook, notification) when that happens, or does the client's next payment just start failing with no advance warning we could surface to them?
12. For special/restricted account types requiring a written request to enable registration — is there a way for us to detect this case programmatically before onboarding, or only by a failed registration attempt?

## 4. Scope relevant to our disbursement model

13. Onsumm.ai is an **adhoc** payment integration — no pre-registered beneficiaries, paying a constantly-changing set of supplier accounts per client. Confirm adhoc/unregistered-beneficiary payments are fully supported for **IMPS** specifically (not just NEFT/RTGS), at production transaction limits, with no per-day or per-new-payee cooling-off period that would block same-day vendor payments.
14. Are there transaction/value limits tied specifically to the **Adhoc Payment (PMT)** access grant, separate from general CIB payment limits? Are those limits exposed to us via API/registration response, or only discoverable by a failed transaction?
15. INR domestic only, confirmed — no FX via CIB. Is that a hard platform limitation, or would FX require a different ICICI product entirely (relevant only if Onsumm.ai ever needs to support non-INR vendor payments)?

## 5. Reconciliation

16. Confirm the first-15-characters `UNIQUEID` truncation used for reconciliation is stable and collision-safe across NEFT/RTGS/IMPS/FT alike, given we'll have many clients' transactions narrated concurrently — what format/prefix convention do you recommend to guarantee uniqueness within 15 characters at our scale?

---

**Priority for the call:** questions 1–9 are the ones that determine whether `disbursement_api.py`'s settlement logic (§7 of `PAYMENT_GUARDRAILS.md`) is safe as currently built, or needs a provisional-success + reconciliation-recheck step added before this goes to production. Everything else is onboarding/scope detail that affects rollout but not the core security model.
