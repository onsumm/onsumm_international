# Lendix Disbursement (IMPS) — UAT Findings & Questions for Lendix

**Context:** `backend_ocr/app/imps_client.py` was wired up on 2026-08-27 to call Lendix's real UAT
gateway (`https://api-dev.lendix.ai`) in place of the previous DEMO/mock implementation
(`disbursement_api.py`), per [`DISBURSEMENT_IMPS_API.md`](../DISBURSEMENT_IMPS_API.md) and
[`DISBURSEMENT_PAYMENT_INTEGRATION_PLAN_LENDIX.md`](./DISBURSEMENT_PAYMENT_INTEGRATION_PLAN_LENDIX.md).
Every real gateway call (request + response, verbatim) is now logged to a new append-only table,
`disbursement_gateway_calls` — the evidence below is pulled directly from that log, not
reconstructed from memory.

The integration itself — endpoint, auth header, request shape, response parsing, guardrails,
audit trail — is confirmed working: a call using the doc's own sample beneficiary
(`beneficiaryAccount: 2212249339904050`, `beneficiaryIFSC: NPCI0000001`, `nbfcName: SOLOMON`,
`remitterAccountNumber: 010101000`) returned a real `SUCCESS` with a real `bankRRN`
(`954411553834`) and `referenceId` (`d6093a2d-ec41-452b-841d-27ca48c17286`), 2026-08-27 05:35 UTC.

**Update 2026-08-27:** we obtained ICICI's own IMPS ActCode / API-Gateway error-code reference
([`ICICI_IMPS_Onboarding_API_Documentation.md`](./ICICI_IMPS_Onboarding_API_Documentation.md)) and
built it into `backend_ocr/app/icici_act_codes.py`, so `status_message` is now a decoded
explanation rather than a raw pass-through string. That table answered one of the questions
originally listed here (removed, with the answer noted inline) and reframed another. What's left
is genuinely still open: our real-vendor-account failures, which came back with **no ActCode to
decode in the first place**, and one status-check response that still doesn't fully add up.

---

## 1. Real vendor bank accounts fail with a generic ICICI 500 — no ActCode returned at all

With NBFC provisioning resolved (`SOLOMON` + `010101000`), we tried two **real, distinct** vendor
bank accounts (different vendors, different banks, both GSTIN+KYC+bank-verified on our side) and
both failed identically:

| Beneficiary | Account | IFSC (bank) | `tranRefNo` | Result |
|---|---|---|---|---|
| RANA ENTERPRISES | `172602000000309` | `IOBA0001726` (Indian Overseas Bank) | `PAY77984990817F44` | `400: All vendors failed. Last error: ICICI API error (500)` |
| GAURAV GROVER | `39183301668` | `SBIN0004688` (State Bank of India) | `PAY11ADC16977F54E` | `400: All vendors failed. Last error: ICICI API error (500)` |

Both attempts: 2026-08-27, `bankProvider: ICICI`, amount ₹1–2 (within the documented ₹10 UAT
ceiling). Meanwhile the doc's own sample beneficiary (`2212249339904050` / `NPCI0000001`) succeeds
every time. **We now have ICICI's full ActCode table** (0–924, U01–UQ1, plus API-Gateway codes
501/401/429/403/997/8010–8099/8123/8096) and neither response carried any of those codes — a bare
"500" isn't in either table, so we can't self-diagnose this one from documentation alone.

**Questions:**
1. Is the UAT/`api-dev.lendix.ai` ICICI integration only provisioned to accept a fixed set of known
   test beneficiary accounts (like the doc's own sample), rather than validating against the real
   IMPS/NPCI beneficiary network? If so, is there a published list of UAT-safe test accounts we
   should use for further integration testing instead of real vendor accounts?
2. A bare "ICICI API error (500)" doesn't map to anything in ICICI's own ActCode or API-Gateway
   error tables — is there a more specific code/reason available on your side (support/ops view)
   for why these two accounts were rejected, or is "500" itself all ICICI returned to Lendix?
3. Is this specific to `bankProvider: ICICI`, or would `AUBANK` behave differently for the same
   real beneficiary accounts? (Docs list AU Bank as secondary/fallback — we haven't tried pinning
   to it directly yet.)
4. Given production presumably *must* accept arbitrary real vendor accounts, is "real accounts
   fail in UAT" expected UAT-tier behavior, or a genuine account-level problem we should treat as a
   red flag before going to production?

## 3. General


5. Are there separate, documented UAT-safe test beneficiary accounts beyond the one sample in
   `DISBURSEMENT_IMPS_API.md` (`2212249339904050` / `NPCI0000001`), so we can exercise more of the
   success/pending/failure paths without needing real vendor accounts to succeed?

---

**Priority:** questions 1–4 (§1) block any further real end-to-end UAT testing with real vendor
accounts — right now every real account we've tried fails identically with an undocumented "500,"
and we can't tell whether that's expected UAT-tier behavior or something to fix before this
integration is usable beyond the single documented sample account.
