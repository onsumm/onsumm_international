# Money Gate — OTP / MFA

**Stage:** 04 · Workflow & human-in-the-loop
**Role:** The non-negotiable checkpoint — no rupee moves without a named approver

## What it does

The non-negotiable checkpoint. A disbursement is created in status `PENDING_OTP` with a **hashed OTP** and an expiry; nothing leaves the bank until a named human verifies it. Any manual override (e.g. substituting a beneficiary account when a vendor isn't yet bank-verified) is itself recorded, so every rupee traces to a person and a reason — never applied silently.

## Capabilities

- `otp_code_hash` + `otp_expires_at` + `otp_attempts` — the OTP itself is never stored in plaintext, and both expiry and attempt-count are enforced
- Status gate: `PENDING_OTP → COMPLETED` — a disbursement literally cannot reach a paid state without passing through this gate
- Overrides are logged, not silent — a substituted beneficiary account always has an audit trail explaining who changed it and why

## Data model

### `fact_disbursements`
`status`, `otp_code_hash`, `otp_verified_at`, `initiated_by`. The disbursement record itself, gated by OTP state.

### `disbursement_overrides`
Who, why, when. Every manual override to beneficiary details, with the reason recorded — this is the exception path (used when a vendor's bank details aren't yet verified) and it's never invisible.

## Where it fits in the pipeline

**Upstream:** the [Workflow engine](./workflow-engine.md) — a bill must have completed its approval chain before a disbursement is even created.
**Downstream:** once OTP-verified, the disbursement proceeds to [Disbursement (payout)](../05-disburse-post-learn/disbursement.md).

## Why it matters

This is the single point in the whole pipeline where automation stops and a human is structurally required — not by convention, but because the disbursement's own status literally cannot advance past `PENDING_OTP` without a verified OTP tied to a specific person. Combined with `disbursement_overrides` never applying silently, this is what makes the claim "every rupee traces to a person and a reason" actually true at the schema level, not just a policy statement. See `docs/PAYMENT_GUARDRAILS.md` for the full guardrail spec this gate is part of.
