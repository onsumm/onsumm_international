# Disbursement

**Stage:** 05 · Disburse, post & learn
**Role:** The actual payout, once the money gate has cleared

## What it does

On approval, the payment is released via bank rails (default ICICI) and the **bank RRN / transaction reference** is captured back onto the disbursement row. Remitter balance and beneficiary details are verified up front; the transaction is unique per `(tenant, tran_ref_no)`, preventing duplicate payouts for the same reference.

## Capabilities

- `bank_rrn` + `bank_tran_ref_no` captured directly onto the disbursement record — the settlement proof lives with the disbursement, not in a separate reconciliation step
- Remitter balance & IFSC checked before the payout fires
- `tran_ref_no` unique per tenant — structurally prevents double-paying the same reference

## Data model

### `fact_disbursements`
`beneficiary_*`, `remitter_account_number`, `bank_rrn`, `status`. The full disbursement record, from initiation through settlement proof.

### `remitter_accounts`
`account`, `ifsc`, `balance`, `provider`. The source account(s) money is paid from, with balance tracked so a payout can't be attempted against insufficient funds.

## Where it fits in the pipeline

**Upstream:** [Money gate — OTP/MFA](../04-workflow-hitl/money-gate-otp.md) — a disbursement only reaches this stage once OTP-verified.
**Downstream:** [ERP write-back](./erp-writeback.md) posts the settled payment to the tenant's books; the outcome also feeds the [Learn loop](./learn-loop.md).

## Why it matters

Capturing the bank RRN directly on the disbursement row (rather than in a separate downstream reconciliation pass) means there's never a window where a payment is "probably done" but unverified — the settlement proof and the payment request live in the same auditable record from the moment the bank confirms it.
